"""
POST /api/transcribe/mrc1  — N対1 転記ジョブ受付（非同期）
GET  /api/jobs/{job_id}    — ジョブ進捗確認

複数ファイル（Excel / Word / PDF）を受け取り、MRC1 に転記する。
Gemini 呼び出しが N 回発生するため BackgroundTasks で非同期化する。
ジョブ状態は in-memory dict で管理（PoC 用。本番は Redis 等に置き換え）。
"""
import logging
import tempfile
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from apps.backend.app.agents.data_extractor.mapper import map_to_schema_from_doc
from apps.backend.app.core.settings import OUTPUT_DIR, TEMPLATE_PATH
from apps.backend.app.merger.field_merger import merge_extractions
from apps.backend.app.readers.source_document import select_reader
from apps.backend.app.tools.formula_executor import execute_formula
from apps.backend.app.pipelines.form_generation_pipeline import generate_form_from_dict

logger = logging.getLogger(__name__)
router = APIRouter()

# PoC 用インメモリジョブストア（本番は Redis に置き換え）
job_store: dict[str, dict] = {}

_SUPPORTED_SUFFIXES = {".xlsx", ".xls", ".docx", ".pdf"}


@router.post("/transcribe/mrc1")
async def transcribe_mrc1(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    sheet: str = Form(default="MRC1"),
    frame: str = Form(default="frameB"),
):
    """
    N対1 転記ジョブを受け付けて即 job_id を返す。

    フロントエンドは GET /api/jobs/{job_id} で 2 秒ごとにポーリングし、
    status=completed になったら result を表示する（タイムアウト推奨: 120 秒）。
    """
    # ファイル形式バリデーション
    for f in files:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in _SUPPORTED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"未対応のファイル形式: {f.filename}。対応: {', '.join(_SUPPORTED_SUFFIXES)}",
            )

    job_id = str(uuid.uuid4())
    job_store[job_id] = {"status": "running", "progress": 0, "result": None}

    # BackgroundTask に渡す前にファイル中身を読み込む（UploadFile はリクエスト終了後に閉じる）
    file_contents: list[tuple[str, bytes]] = [
        (f.filename or f"file_{i}", await f.read())
        for i, f in enumerate(files)
    ]

    background_tasks.add_task(
        _run_transcription_pipeline,
        job_id=job_id,
        file_contents=file_contents,
        sheet=sheet,
        frame=frame,
    )

    return {"job_id": job_id, "status": "accepted"}


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """ジョブの進捗・結果を返す。フロントエンドはこれをポーリングする。"""
    if job_id not in job_store:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_store[job_id]


def _run_transcription_pipeline(
    job_id: str,
    file_contents: list[tuple[str, bytes]],
    sheet: str,
    frame: str,
) -> None:
    """
    実際の転記処理（BackgroundTasks からスレッドプールで実行される）。

    【重要】sync def にすること。async def にすると call_gemini（同期）が
    イベントループを 30-60 秒占有して他リクエストが固まる。
    """
    try:
        n_files = len(file_contents)
        progress_per_file = 50 // max(n_files, 1)

        # ── STEP 1: ファイルを SourceDocument に変換 ──────────────────────────
        source_docs = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for filename, content in file_contents:
                tmp_path = Path(tmpdir) / filename
                tmp_path.write_bytes(content)

                try:
                    reader_fn = select_reader(str(tmp_path))
                    doc = reader_fn(str(tmp_path))
                    source_docs.append(doc)
                except ValueError as e:
                    logger.warning(f"[transcribe] {filename} の読み込みをスキップ: {e}")

                job_store[job_id]["progress"] = min(
                    job_store[job_id]["progress"] + progress_per_file, 50
                )

            # ── STEP 2: Gemini で各ドキュメントから抽出 ──────────────────────
            extractions = []
            for doc in source_docs:
                result = map_to_schema_from_doc(doc, sheet_name=sheet, frame_name=frame)
                extractions.append({
                    "source_file": doc.source_file,
                    "document_kind": doc.document_kind,
                    "data": result.get("extracted_data", {}),
                    "_metadata": result.get("field_metadata", {}),
                    "formula_specs": result.get("formula_specs", []),
                })

        job_store[job_id]["progress"] = 70

        # ── STEP 3: formula_executor で計算仕様を検証 ─────────────────────────
        formula_results = []
        conflicts: list[dict] = []

        for ext in extractions:
            for spec in ext.get("formula_specs", []):
                fr = execute_formula(spec)
                formula_results.append(fr)
                if fr.needs_review:
                    conflicts.append({
                        "type": "formula_inconsistency",
                        "formula_name": fr.formula_name,
                        "python_result": fr.python_result,
                        "gemini_result": fr.gemini_result,
                        "note": fr.discrepancy_note,
                        "source_location": fr.source_location,
                    })

        # ── STEP 4: N:1 マージ ────────────────────────────────────────────────
        merged, field_conflicts = merge_extractions(extractions)
        conflicts.extend(field_conflicts)

        job_store[job_id]["progress"] = 85

        # ── STEP 5: MRC1 に書き込み ───────────────────────────────────────────
        # merged は { field: {value, source_file, ...} } 形式なので
        # generate_form_from_dict が期待する { field: value } に変換
        input_data: dict = {}
        source_metadata: dict = {}
        for field_name, field_info in merged.items():
            input_data[field_name] = field_info["value"]
            source_metadata[field_name] = {
                "source_location": field_info.get("source_location"),
                "confidence": field_info.get("confidence"),
            }

        output_path = str(OUTPUT_DIR / f"MRC1_{job_id[:8]}.xlsx")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        cell_mappings, processed_sheets = generate_form_from_dict(
            input_data=input_data,
            source_metadata=source_metadata,
            template_excel_path=str(TEMPLATE_PATH),
            result_excel_path=output_path,
            frame_name=frame,
            source_filename="（複数ファイル）",
        )

        # ── STEP 6: ジョブ完了 ────────────────────────────────────────────────
        # cell_mappings から skipped_cells を推定（writable:false でスキップされたもの）
        # generate_form_from_dict は現時点では skipped_cells を返さないため
        # YAML から writable:false フィールドを列挙して補完する
        skipped_cells = _collect_skipped_cells(frame, sheet)

        job_store[job_id] = {
            "status": "completed",
            "progress": 100,
            "result": {
                "output_path": output_path,
                "cell_mappings": cell_mappings,
                "skipped_cells": skipped_cells,
                "conflicts": conflicts,
                "formula_results": [
                    {
                        "name": fr.formula_name,
                        "consistent": fr.is_consistent,
                        "python_result": fr.python_result,
                        "gemini_result": fr.gemini_result,
                        "source_location": fr.source_location,
                    }
                    for fr in formula_results
                ],
            },
        }

    except Exception as e:
        logger.exception(f"[transcribe] job_id={job_id} でエラーが発生しました")
        job_store[job_id] = {
            "status": "failed",
            "progress": 0,
            "error": str(e),
        }


@router.get("/download-job/{job_id}")
async def download_job_result(job_id: str):
    """転記結果の Excel ファイルをダウンロードする。"""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != "completed":
        raise HTTPException(status_code=400, detail=f"Job is not completed (status: {job.get('status')})")

    output_path = job.get("result", {}).get("output_path")
    if not output_path or not Path(output_path).exists():
        raise HTTPException(status_code=404, detail="Output file not found")

    return FileResponse(
        path=output_path,
        filename=Path(output_path).name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _collect_skipped_cells(frame: str, sheet: str) -> list[str]:
    """YAML の extraction_schema から writable:false のフィールド名を返す。"""
    try:
        from apps.backend.app.core.frame_config_loader import load_frame_config
        config = load_frame_config(frame, sheet)
        schema = config.get("extraction_schema", {})
        return [name for name, defn in schema.items() if not defn.get("writable", True)]
    except FileNotFoundError:
        return []
