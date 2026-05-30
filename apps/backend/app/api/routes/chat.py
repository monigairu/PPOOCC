"""
チャット関連エンドポイント

POST /api/chat - Q&A・編集指示・曖昧応答を統合処理する
"""
import logging

from fastapi import APIRouter, HTTPException
from google.cloud import firestore

from apps.backend.app.agents.chat_editor.chat_editor_agent import (
    apply_cell_edit,
    handle_unified_chat,
)
from apps.backend.app.api.models import (
    ChatRequest,
    ChatResponse,
    EditedCell,
)
from apps.backend.app.core.ai_client import call_gemini
from apps.backend.app.core.firestore_client import get_firestore_client
from apps.backend.app.core.frame_config_loader import extract_cell_definitions, load_frame_config
from apps.backend.app.core.gcs_client import upload_file
from apps.backend.app.core.settings import GCS_BUCKET_NAME, OUTPUT_DIR

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    統合チャットエンドポイント。Q&A と編集指示を自動判別する。

    - 「なぜ○○なの？」  → 転記根拠をもとに説明（answer）
    - 「○○を△△に変えて」→ セルを書き換えて結果を返す（edited）
    - 値が不明など曖昧  → 確認質問を返す（ambiguous）
    """
    # 1. 編集可能フィールド一覧を YAML から取得
    try:
        config = load_frame_config(request.frame_name, request.sheet_name)
        yaml_cell_defs = extract_cell_definitions(config)
        available_fields = list(yaml_cell_defs.keys())
    except FileNotFoundError:
        available_fields = []

    # 2. 意図判定 + 応答生成（LLM 1回）
    result = handle_unified_chat(
        user_message=request.message,
        available_fields=available_fields,
        field_name=request.field_name,
        cell_address=request.cell_address,
        field_value=request.field_value,
        reasoning=request.reasoning,
    )

    # 3. 質問への回答
    if result.type == "answer":
        return ChatResponse(type="answer", answer=result.answer or "")

    # 4. 曖昧な編集指示
    if result.type == "ambiguous":
        return ChatResponse(
            type="ambiguous",
            answer=result.clarification_question or "どのフィールドをどの値に変更しますか？",
        )

    # 5. 編集指示: フィールド確認 → セル書き込み
    if not result.field or result.field not in available_fields:
        return ChatResponse(
            type="answer",
            answer=f"フィールド「{result.field}」が見つかりませんでした。変更できるフィールド: {', '.join(available_fields[:8])}...",
        )

    if not result.new_value:
        return ChatResponse(
            type="ambiguous",
            answer=f"「{result.field}」をどの値に変更しますか？",
        )

    if not request.session_id:
        return ChatResponse(
            type="answer",
            answer="セッション情報がないため編集できません。ファイルをアップロードしてからお試しください。",
        )

    try:
        edit_result = apply_cell_edit(
            session_id=request.session_id,
            field_name=result.field,
            new_value=result.new_value,
            frame_name=request.frame_name,
            sheet_name=request.sheet_name,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("セル書き込みに失敗しました session_id=%s", request.session_id)
        raise HTTPException(status_code=500, detail=f"セルの書き込みに失敗しました: {e}")

    _update_firestore_mappings(
        session_id=request.session_id,
        field_name=result.field,
        new_value=result.new_value,
    )
    _reupload_excel_to_gcs(
        session_id=request.session_id,
        frame_name=request.frame_name,
    )

    cells_str = "、".join(edit_result.cell_addresses)
    return ChatResponse(
        type="edited",
        answer=f"「{result.field}」を「{result.new_value}」に変更しました（セル: {cells_str}）",
        edited_cells=[
            EditedCell(
                field_name=edit_result.field_name,
                cell_addresses=edit_result.cell_addresses,
                new_value=edit_result.new_value,
            )
        ],
    )


def _update_firestore_mappings(
    session_id: str,
    field_name: str,
    new_value: str,
) -> None:
    """
    Firestore のセッションドキュメント内 mappings 配列を更新する。

    同一 field_name のエントリをすべて新しい値に置き換える。
    失敗してもセルへの書き込み結果は返却済みのためログのみ。
    """
    try:
        db = get_firestore_client()
        doc_ref = db.collection("sessions").document(session_id)
        doc = doc_ref.get()
        if not doc.exists:
            logger.warning("Firestore セッションが見つかりません: %s", session_id)
            return

        mappings: list[dict] = doc.to_dict().get("mappings", [])
        updated = [
            {**m, "value": new_value} if m.get("field_name") == field_name else m
            for m in mappings
        ]
        doc_ref.update({
            "mappings": updated,
            "last_edited_at": firestore.SERVER_TIMESTAMP,
        })
    except Exception as e:
        logger.warning("Firestore の mappings 更新に失敗しました（session_id=%s）: %s", session_id, e)


def _reupload_excel_to_gcs(session_id: str, frame_name: str) -> None:
    """
    編集済み Excel を GCS の同一パスに上書きアップロードする。

    GCS パスは Firestore の output_gcs_path から取得する。
    失敗してもログのみで処理を継続する。
    """
    try:
        db = get_firestore_client()
        doc = db.collection("sessions").document(session_id).get()
        if not doc.exists:
            return

        gcs_path: str | None = doc.to_dict().get("output_gcs_path")
        if not gcs_path or not GCS_BUCKET_NAME:
            return

        local_path = OUTPUT_DIR / f"result_{frame_name}_{session_id}.xlsx"
        if not local_path.exists():
            return

        upload_file(
            bucket_name=GCS_BUCKET_NAME,
            blob_path=gcs_path,
            local_path=str(local_path),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        logger.info("編集済み Excel を GCS に再アップロードしました: %s", gcs_path)
    except Exception as e:
        logger.warning("GCS 再アップロードに失敗しました（session_id=%s）: %s", session_id, e)
