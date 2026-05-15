"""
POST /api/upload

アップロードされたファイルを受け取り、NuRO様式を自動生成する。

このモジュールはファイル受付・データ抽出・HTTPレスポンス構築のみを担当する。
Excel書き込みの実処理は form_generation_pipeline.generate_form_from_dict() に委譲する。
"""
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Path as FastAPIPath, Query
from google.cloud import firestore

from apps.backend.app.api.models import UploadResponse, CellMapping
from apps.backend.app.agents.data_extractor.data_extractor_agent import extract_data
from apps.backend.app.core.firestore_client import get_firestore_client
from apps.backend.app.core.settings import OUTPUT_DIR, UPLOAD_DIR, TEMPLATE_PATH
from apps.backend.app.pipelines.form_generation_pipeline import generate_form_from_dict

router = APIRouter()

SUPPORTED_EXTENSIONS = {".json", ".xlsx", ".xls", ".docx"}


@router.post("/upload", response_model=UploadResponse)
async def upload_and_generate(
    file: UploadFile = File(...),
    sheet_name: str = Form(default="MRC1", pattern=r"^[a-zA-Z0-9_\-]+$"),
    frame_name: str = Form(default="frameB", pattern=r"^[a-zA-Z0-9_\-]+$"),
    utility_name: str = Form(default="未設定"),
    # TODO(Step2): フロントエンド実装時に電力会社名の入力欄を追加して
    #              utility_name を必須パラメータとして送信するよう修正すること
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
    # pathlib.Path(filename).suffixを使って安全に拡張子を取得
    suffix = Path(filename).suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"未対応のファイル形式です: {suffix}。対応形式: {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    content = await file.read()

    # パストラバーサル対策: pathlib.Path().nameでサニタイズ
    safe_frame = Path(frame_name).name
    safe_sheet = Path(sheet_name).name

    # ── データ抽出 ──────────────────────────────
    try:
        if suffix == ".json":
            input_data = json.loads(content)
            source_metadata: dict = {}
            print(f"   JSONファイルを直接読み込みました: {filename}")
        else:
            input_data, source_metadata = _extract_from_file(
                content, filename, suffix, safe_sheet, safe_frame
            )
            print(f"   {suffix}ファイルからデータを抽出しました: {filename}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"JSONの読み込みに失敗しました: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"ファイルの読み込みに失敗しました: {e}")

    # ── Excel生成（pipelineに委譲）──────────────
    session_id = str(uuid.uuid4())  # ③ 全桁使用（8文字切り捨てを廃止）
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

    # Firestoreにセッション情報を保存（レビュー機能との連携用）
    _save_session_to_firestore(
        session_id=session_id,
        utility_name=utility_name,
        frame_name=safe_frame,
        sheet_name=primary_sheet,
        mappings=raw_mappings,
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


def _save_session_to_firestore(
    session_id: str,
    utility_name: str,
    frame_name: str,
    sheet_name: str,
    mappings: list[dict],
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
            "frame_name": frame_name,
            "sheet_name": sheet_name,
            "mappings": mappings,
            "created_at": firestore.SERVER_TIMESTAMP,
            "reviewed": False,
        })
    except Exception as e:
        # Firestoreへの保存失敗は転記結果を壊さない
        print(f"[WARNING] Firestoreへのセッション保存に失敗しました（session_id={session_id}）: {e}")


@router.get("/download/{session_id}")
async def download_result(
    session_id: str = FastAPIPath(..., pattern=r"^[a-f0-9\-]{8,36}+$"),
    frame_name: str = Query("frameB", pattern=r"^[a-zA-Z0-9_\-]+$"),
    sheet_name: str = Query("MRC1", pattern=r"^[a-zA-Z0-9_\-]+$"),
):
    """転記済みExcelファイルをダウンロードする。"""
    from fastapi.responses import FileResponse

    # pathlib.Path.nameを使用してサニタイズ(パストラバーサル対策)
    safe_frame = Path(frame_name).name
    safe_session = Path(session_id).name
    safe_sheet = Path(sheet_name).name

    result_path = OUTPUT_DIR / f"result_{safe_frame}_{safe_session}.xlsx"
    if not result_path.exists():
        # 旧形式（8文字session_id または sheet_name 使用）へのフォールバック
        result_path = OUTPUT_DIR / f"result_{safe_sheet}_{safe_session}.xlsx"

    if not result_path.exists():
        raise HTTPException(status_code=404, detail="ファイルが見つかりません")

    return FileResponse(
        path=str(result_path),
        filename=f"転記結果_{safe_frame}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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

    ①② 一時ファイル名にUUIDを使用してパストラバーサルと競合を防ぐ。

    Returns:
        (input_data, source_metadata)
        - input_data:      { フィールド名: 値 } の転記用辞書
        - source_metadata: { フィールド名: { source_location, confidence, ... } }
    """
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # suffix をバリデーション済みのSUPPORTED_EXTENSIONSに限定し、かつPath.nameでサニタイズ
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"未対応のファイル形式です: {suffix}")
    safe_suffix = Path(suffix).name
    # ①② ファイル名をそのまま使わずUUIDで生成（パストラバーサル・競合防止）
    temp_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{safe_suffix}"

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
