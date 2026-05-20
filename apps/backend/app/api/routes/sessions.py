"""
セッション管理エンドポイント（転記ページの履歴サイドバー用）

GET /api/sessions                        - 全セッション一覧（review_status・進捗付き）
GET /api/sessions/{session_id}/mappings  - セッションの転記結果を返す

review_status の定義:
    "not_reviewed" : レビュー未実行
    "in_progress"  : レビュー実行済み・未判定指摘あり（decided_count < total_count）
    "completed"    : 全指摘に承諾/棄却済み（decided_count >= total_count）
"""
from fastapi import APIRouter, HTTPException
from google.cloud import firestore

from apps.backend.app.core.firestore_client import get_firestore_client

router = APIRouter()


@router.get("/sessions")
async def list_sessions_with_status():
    """
    全セッション一覧を review_status・進捗情報付きで返す。

    転記ページの履歴サイドバー（未レビュー/レビュー済み）向け。
    各セッションごとに最新 review_result を1件取得して進捗を算出する。
    """
    db = get_firestore_client()

    docs = (
        db.collection("sessions")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(50)
        .stream()
    )

    result = []
    for doc in docs:
        data = doc.to_dict()
        session_id = data.get("session_id", doc.id)

        created_at = data.get("created_at")
        if hasattr(created_at, "isoformat"):
            created_at_str = created_at.isoformat()
        else:
            created_at_str = str(created_at) if created_at else ""

        # 最新 review_result から進捗を算出
        result_docs = (
            db.collection("sessions").document(session_id)
            .collection("review_results")
            .order_by("reviewed_at", direction=firestore.Query.DESCENDING)
            .limit(1)
            .stream()
        )
        result_doc = next(result_docs, None)

        if result_doc is None:
            review_status = "not_reviewed"
            progress = None
        else:
            rd = result_doc.to_dict()
            total = rd.get("total_count", 0)
            decided = rd.get("decided_count", 0)
            # 手動保存フラグ、または全件判定済みで completed
            manually_saved = data.get("review_completed", False)
            if manually_saved or (total > 0 and decided >= total):
                review_status = "completed"
            else:
                review_status = "in_progress"
            progress = {"total": total, "decided": decided}

        result.append({
            "session_id": session_id,
            "session_name": data.get("session_name", ""),
            "utility_name": data.get("utility_name", "未設定"),
            "frame_name": data.get("frame_name", "frameB"),
            "sheet_name": data.get("sheet_name", "MRC1"),
            "created_at": created_at_str,
            "review_status": review_status,
            "progress": progress,
        })

    return result


@router.patch("/sessions/{session_id}/complete")
async def complete_session_review(session_id: str):
    """
    レビュー結果を手動で保存済みにする。

    指摘の承諾/棄却が途中でも「レビュー済み」セクションに移動させたい場合に使用する。
    セッションに review_completed フラグを立てる。
    """
    db = get_firestore_client()
    session_ref = db.collection("sessions").document(session_id)
    if not session_ref.get().exists:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")
    session_ref.update({"review_completed": True})
    return {"status": "completed"}


@router.get("/sessions/{session_id}/mappings")
async def get_session_mappings(session_id: str):
    """
    セッションの転記結果（mappings）を返す。

    転記ページで履歴セッションを選択したときにmappingsを復元するために使用する。
    """
    db = get_firestore_client()
    doc = db.collection("sessions").document(session_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")

    data = doc.to_dict()
    return {
        "session_id": session_id,
        "session_name": data.get("session_name", ""),
        "utility_name": data.get("utility_name", ""),
        "frame_name": data.get("frame_name", "frameB"),
        "sheet_name": data.get("sheet_name", "MRC1"),
        "mappings": data.get("mappings", []),
    }
