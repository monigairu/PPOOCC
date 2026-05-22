"""
POST /api/upload

アップロードされたファイルを受け取り、NuRO様式を自動生成する。

このモジュールはファイル受付・データ抽出・HTTPレスポンス構築のみを担当する。
Excel書き込みの実処理は form_generation_pipeline.generate_form_from_dict() に委譲する。
"""
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Path as FastAPIPath, Query
from google.cloud import firestore

from apps.backend.app.api.models import UploadResponse, CellMapping
from apps.backend.app.agents.data_extractor.data_extractor_agent import extract_data
from apps.backend.app.core.firestore_client import get_firestore_client
from apps.backend.app.core.gcs_client import (
    generate_signed_url,
    sanitize_path_component,
    upload_bytes,
    upload_file,
)
from apps.backend.app.core.settings import GCS_BUCKET_NAME, OUTPUT_DIR, UPLOAD_DIR, TEMPLATE_PATH
from apps.backend.app.pipelines.form_generation_pipeline import generate_form_from_dict

logger = logging.getLogger(__name__)
router = APIRouter()

SUPPORTED_EXTENSIONS = {".json", ".xlsx", ".xls", ".docx"}
_EXCEL_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("/upload", response_model=UploadResponse)
async def upload_and_generate(
    file: UploadFile = File(...),
    sheet_name: str = Form(default="MRC1", pattern=r"^[a-zA-Z0-9_\-]+$"),
    frame_name: str = Form(default="frameB", pattern=r"^[a-zA-Z0-9_\-]+$"),
    utility_name: str = Form(default="未設定"),
):
    """
    ファイルをアップロードしてNuRO様式を自動生成する。

    対応形式:
        - .json  → そのまま転記データとして使用
        - .xlsx  → data_extractorでJSONに変換してから転記
        - .docx  → data_extractorでJSONに変換してから転記

    frame_name 配下の全YAML定義シートを処理する。
    転記完了後にFirestoreへセッション情報を保存する（レビュー機能との連携用）。
    """
    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"未対応のファイル形式です: {suffix}。対応形式: {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    content = await file.read()

    safe_frame = Path(frame_name).name
    safe_sheet = Path(sheet_name).name
    session_id = str(uuid.uuid4())

    # ── ① 原本ファイルをGCSにアップロード ───────────────────────
    safe_utility = sanitize_path_component(utility_name)
    input_blob_path = f"uploads/{safe_utility}/{session_id}/original{suffix}"
    try:
        upload_bytes(
            bucket_name=GCS_BUCKET_NAME,
            blob_path=input_blob_path,
            content=content,
            content_type=_EXCEL_CONTENT_TYPE if suffix in (".xlsx", ".xls") else "application/octet-stream",
        )
        logger.info("原本をGCSにアップロード: %s", input_blob_path)
    except Exception as e:
        logger.warning("原本のGCSアップロードに失敗しました（処理は続行）: %s", e)

    # ── データ抽出 ──────────────────────────────
    try:
        if suffix == ".json":
            input_data = json.loads(content)
            source_metadata: dict = {}
            logger.info("JSONファイルを直接読み込みました: %s", filename)
        else:
            input_data, source_metadata = _extract_from_file(
                content, filename, suffix, safe_sheet, safe_frame
            )
            logger.info("%sファイルからデータを抽出しました: %s", suffix, filename)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"JSONの読み込みに失敗しました: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"ファイルの読み込みに失敗しました: {e}")

    # ── Excel生成（pipelineに委譲）──────────────
    result_path = str(OUTPUT_DIR / f"result_{safe_frame}_{session_id}.xlsx")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        raw_mappings, processed_sheets = generate_form_from_dict(
            input_data=input_data,
            source_metadata=source_metadata,
            template_excel_path=str(TEMPLATE_PATH),
            result_excel_path=result_path,
            frame_name=safe_frame,
            source_filename=filename,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Excel生成に失敗しました: {e}")

    cell_mappings = [CellMapping(**m) for m in raw_mappings]
    primary_sheet = processed_sheets[0] if processed_sheets else sheet_name
    sheets_label = ", ".join(processed_sheets) if processed_sheets else sheet_name

    # ── ② 転記済みExcelをGCSにアップロード ──────────────────────
    output_gcs_path: str | None = None
    output_blob_path = f"outputs/{safe_utility}/{session_id}/MRC1_filled.xlsx"
    try:
        output_gcs_path = upload_file(
            bucket_name=GCS_BUCKET_NAME,
            blob_path=output_blob_path,
            local_path=result_path,
            content_type=_EXCEL_CONTENT_TYPE,
        )
        logger.info("転記済みExcelをGCSにアップロード: %s", output_gcs_path)
    except Exception as e:
        logger.warning("転記済みExcelのGCSアップロードに失敗しました（ローカルファイルは残存）: %s", e)

    # ── Firestoreにセッション情報を保存 ─────────────────────────
    session_name = _extract_session_name(raw_mappings, filename)
    _save_session_to_firestore(
        session_id=session_id,
        utility_name=utility_name,
        frame_name=safe_frame,
        sheet_name=primary_sheet,
        mappings=raw_mappings,
        session_name=session_name,
        output_gcs_path=output_gcs_path,
    )

    return UploadResponse(
        session_id=session_id,
        frame_name=safe_frame,
        sheet_name=primary_sheet,
        mappings=cell_mappings,
        message=(
            f"{len(cell_mappings)}件のセルへの転記が完了しました"
            f"（入力: {filename}、シート: {sheets_label}）"
        ),
    )


def _extract_session_name(raw_mappings: list[dict], filename: str) -> str:
    """転記結果から表示用セッション名を生成する。工事件名 > ファイル名の順で採用。"""
    for field in ("工事件名", "件名", "工事名"):
        for m in raw_mappings:
            if m.get("field_name") == field:
                val = str(m.get("value", "")).strip()
                if val and val not in ("なし", "N/A", "-", ""):
                    return val[:40]
    return Path(filename).stem[:40]


def _save_session_to_firestore(
    session_id: str,
    utility_name: str,
    frame_name: str,
    sheet_name: str,
    mappings: list[dict],
    session_name: str = "",
    output_gcs_path: str | None = None,
) -> None:
    """
    転記完了後にセッション情報をFirestoreへ保存する。
    Firestoreへの保存失敗は転記結果の返却を妨げない（ログ出力のみ）。
    """
    try:
        db = get_firestore_client()
        db.collection("sessions").document(session_id).set({
            "session_id": session_id,
            "utility_name": utility_name,
            "session_name": session_name,
            "frame_name": frame_name,
            "sheet_name": sheet_name,
            "mappings": mappings,
            "created_at": firestore.SERVER_TIMESTAMP,
            "reviewed": False,
            "input_gcs_path": None,
            "output_gcs_path": output_gcs_path,
        })
    except Exception as e:
        logger.warning("Firestoreへのセッション保存に失敗しました（session_id=%s）: %s", session_id, e)


@router.get("/download/{session_id}")
async def download_result(
    session_id: str = FastAPIPath(..., pattern=r"^[a-f0-9\-]{8,36}+$"),
    frame_name: str = Query("frameB", pattern=r"^[a-zA-Z0-9_\-]+$"),
):
    """
    転記済みExcelの署名付きURL（有効期限15分）を返す。

    FirestoreのセッションからGCSパスを取得し、Signed URLを生成する。
    GCSパスが未登録の場合はローカルファイルへのフォールバックを試みる。
    """
    safe_session = Path(session_id).name

    # FirestoreからGCSパスを取得
    try:
        db = get_firestore_client()
        doc = db.collection("sessions").document(safe_session).get()
        if doc.exists:
            output_gcs_path: str | None = doc.to_dict().get("output_gcs_path")
            utility_name: str = doc.to_dict().get("utility_name", "未設定")
        else:
            output_gcs_path = None
            utility_name = "未設定"
    except Exception as e:
        logger.warning("Firestoreからのセッション取得に失敗: %s", e)
        output_gcs_path = None
        utility_name = "未設定"

    # GCSパスが記録されていれば署名付きURLを生成
    if output_gcs_path and output_gcs_path.startswith("gs://"):
        blob_path = output_gcs_path.removeprefix(f"gs://{GCS_BUCKET_NAME}/")
        try:
            signed_url = generate_signed_url(
                bucket_name=GCS_BUCKET_NAME,
                blob_path=blob_path,
                expiration_minutes=15,
                filename="転記結果.xlsx",
            )
            return {"url": signed_url, "expires_in_minutes": 15}
        except Exception as e:
            logger.error("署名付きURL生成に失敗しました: %s", e)
            raise HTTPException(status_code=500, detail=f"ダウンロードURLの生成に失敗しました: {e}")

    # フォールバック: ローカルファイル（GCS未登録の旧セッション向け）
    safe_frame = Path(frame_name).name
    result_path = OUTPUT_DIR / f"result_{safe_frame}_{safe_session}.xlsx"
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="ファイルが見つかりません")

    from fastapi.responses import FileResponse
    return FileResponse(
        path=str(result_path),
        filename="転記結果.xlsx",
        media_type=_EXCEL_CONTENT_TYPE,
    )


def _extract_from_file(
    content: bytes,
    filename: str,
    suffix: str,
    sheet_name: str,
    frame_name: str,
) -> tuple[dict, dict]:
    """
    Excel/Wordファイルからdata_extractorを使ってJSONデータと出典メタデータを抽出する。

    Returns:
        (input_data, source_metadata)
        - input_data:      { フィールド名: 値 } の転記用辞書
        - source_metadata: { フィールド名: { source_location, confidence, ... } }
    """
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"未対応のファイル形式です: {suffix}")
    temp_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"

    try:
        with open(temp_path, "wb") as f:
            f.write(content)

        result = extract_data(
            source_file=str(temp_path),
            sheet_name=sheet_name,
            frame_name=frame_name,
            verbose=True,
        )

        return result["data"], result.get("_metadata", {})

    finally:
        if temp_path.exists():
            temp_path.unlink()
