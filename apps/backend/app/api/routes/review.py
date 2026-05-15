"""
レビュー関連エンドポイント

POST /api/review          - 転記済みセッションのAIレビューを実行
POST /api/review/{id}/feedback - NuROによる承諾/棄却
GET  /api/review/sessions - 未レビューのセッション一覧（NuRO画面用）
GET  /api/review/stats    - Phase 2 移行判断指標（棄却率・件数トレンド）

PoC の認証方針：
    caller_role は "NuRO" で固定している（/review 画面はNuROのみがアクセスするため）。
    本番移行時は各エンドポイントに user=Depends(get_current_user) を追加し、
    caller_role=user["role"] に変更するだけでよい。
    knowledge_loader.py の変更は不要。

Phase 2 移行判断トリガー（stats エンドポイントで確認）：
    - 棄却率が 50% 超えを継続
    - 月次指摘数が 10 件超（見落とし頻発）
    - ナレッジ量が急増（F3 > 1万件）
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from google.cloud import firestore

from apps.backend.app.agents.reviewer import reviewer_agent
from apps.backend.app.api.models import (
    FeedbackRequest,
    FeedbackResponse,
    ReviewRequest,
    ReviewResponse,
    SessionSummary,
)
from apps.backend.app.core.firestore_client import get_firestore_client

router = APIRouter()


@router.post("/review", response_model=ReviewResponse)
async def run_review(request: ReviewRequest):
    """
    指定セッションの転記結果をAIがレビューして指摘リストを返す。

    PoC：caller_role="NuRO" 固定
    本番移行時：引数に user=Depends(get_current_user) を追加し、
                caller_role=user["role"] に変更する（このファイルのみ修正）
    """
    db = get_firestore_client()

    # Firestore からセッション情報を取得
    session_ref = db.collection("sessions").document(request.session_id)
    session_doc = session_ref.get()
    if not session_doc.exists:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {request.session_id}")

    session_data = session_doc.to_dict()
    mappings: list[dict] = session_data.get("mappings", [])
    if not mappings:
        raise HTTPException(status_code=400, detail="セッションに転記データがありません")

    # AIレビュー実行（Agentic RAG 軽量版: 4 Tool 固定順実行）
    try:
        review_items = await reviewer_agent.run_review(
            session_id=request.session_id,
            utility_name=request.utility_name,
            mappings=mappings,
            frame_name=request.frame_name,
            sheet_name=request.sheet_name,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"レビュー処理に失敗しました: {e}")

    review_id = str(uuid.uuid4())
    reviewed_at = datetime.now(timezone.utc).isoformat()

    # レビュー結果をFirestoreのサブコレクションに保存
    review_ref = session_ref.collection("review_results").document(review_id)
    review_ref.set({
        "review_id": review_id,
        "review_items": [item.model_dump() for item in review_items],
        "summary": _extract_summary_from_items(review_items),
        "reviewed_at": reviewed_at,
        "feedbacks": [],
    })

    # セッションのレビュー済みフラグを更新
    session_ref.update({"reviewed": True})

    return ReviewResponse(
        review_id=review_id,
        review_items=review_items,
        summary=_extract_summary_from_items(review_items),
        reviewed_at=reviewed_at,
        mappings=mappings,
    )


@router.post("/review/{review_id}/feedback", response_model=FeedbackResponse)
async def submit_feedback(review_id: str, request: FeedbackRequest):
    """
    NuROが指摘事項に対して承諾（accept）または棄却（reject）を行う。

    承諾 → Firestoreにfeedbackを保存
    棄却 → セッション内のみで破棄（DBには保存しない）
    """
    if request.decision not in ("accept", "reject"):
        raise HTTPException(status_code=400, detail="decision は 'accept' または 'reject' を指定してください")

    if request.decision == "reject":
        # 棄却もPhase 2移行判断のために集計する（内容はDBに残さない）
        _increment_feedback_stats(db=get_firestore_client(), decision="reject")
        return FeedbackResponse(status="discarded")

    # 承諾の場合: 対応するreview_resultを検索してfeedbackを追記
    db = get_firestore_client()

    # review_id からセッションを逆引き（Firestoreのコレクショングループクエリを使用）
    review_docs = (
        db.collection_group("review_results")
        .where(filter=firestore.FieldFilter("review_id", "==", review_id))
        .limit(1)
        .stream()
    )
    review_doc = next(review_docs, None)
    if review_doc is None:
        raise HTTPException(status_code=404, detail=f"レビュー結果が見つかりません: {review_id}")

    feedback_entry = {
        "item_id": request.item_id,
        "decision": "accept",
        "comment": request.comment,
        "accepted_at": datetime.now(timezone.utc).isoformat(),
    }
    review_doc.reference.update({
        "feedbacks": firestore.ArrayUnion([feedback_entry])
    })

    # 承諾もPhase 2移行判断のために集計する
    _increment_feedback_stats(db=get_firestore_client(), decision="accept")

    return FeedbackResponse(status="saved")


@router.get("/review/sessions", response_model=list[SessionSummary])
async def list_unreviewed_sessions():
    """
    未レビューのセッション一覧を返す（NuROの画面での選択用）。

    PoC：全セッションを返す（認証なし）
    本番移行時：JWT から utility_name を取得してフィルタリングを追加する
    """
    db = get_firestore_client()

    docs = (
        db.collection("sessions")
        .where(filter=firestore.FieldFilter("reviewed", "==", False))
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(50)
        .stream()
    )

    sessions = []
    for doc in docs:
        data = doc.to_dict()
        created_at = data.get("created_at")
        # Firestore の SERVER_TIMESTAMP は datetime 型で返る
        if hasattr(created_at, "isoformat"):
            created_at_str = created_at.isoformat()
        else:
            created_at_str = str(created_at) if created_at else ""

        sessions.append(
            SessionSummary(
                session_id=data.get("session_id", doc.id),
                utility_name=data.get("utility_name", "未設定"),
                frame_name=data.get("frame_name", ""),
                sheet_name=data.get("sheet_name", ""),
                created_at=created_at_str,
                reviewed=data.get("reviewed", False),
            )
        )

    return sessions


@router.delete("/review/{review_id}/feedback/{item_id}")
async def undo_feedback(review_id: str, item_id: str):
    """
    承諾または棄却を取り消して未決定状態に戻す。

    Firestoreの feedbacks 配列から該当 item_id のエントリを削除する。
    棄却時は元々Firestoreに書き込まないため、削除処理は不要（状態リセットのみ）。
    ※ review_stats（棄却率集計）の数値は遡及修正しない（近似値として許容）。
    """
    db = get_firestore_client()

    review_docs = (
        db.collection_group("review_results")
        .where(filter=firestore.FieldFilter("review_id", "==", review_id))
        .limit(1)
        .stream()
    )
    review_doc = next(review_docs, None)

    if review_doc is not None:
        data = review_doc.to_dict()
        # feedbacks 配列から該当 item_id のエントリを除外して上書き
        new_feedbacks = [f for f in data.get("feedbacks", []) if f.get("item_id") != item_id]
        review_doc.reference.update({"feedbacks": new_feedbacks})

    return {"status": "undone"}


@router.get("/review/stats")
async def get_review_stats():
    """
    Phase 2 移行判断指標を返す。

    返却値:
        total_accepted:  累計承諾数
        total_rejected:  累計棄却数
        rejection_rate:  棄却率（0.0〜1.0）
        phase2_trigger:  Phase 2 移行推奨フラグ（棄却率 50% 超えで true）
        daily:           日別の承諾/棄却件数（直近30日分）

    Phase 2 移行判断トリガー（要件書 Section 4より）:
        - rejection_rate >= 0.5 が継続 → phase2_trigger = true
        - monthly_total >= 10 件（見落とし頻発）
        - ナレッジ量急増は別途 knowledge_loader でモニタリング
    """
    db = get_firestore_client()

    stats_docs = (
        db.collection("review_stats")
        .order_by("date", direction=firestore.Query.DESCENDING)
        .limit(30)
        .stream()
    )

    total_accepted = 0
    total_rejected = 0
    daily = []

    for doc in stats_docs:
        data = doc.to_dict()
        accepted = data.get("accepted", 0)
        rejected = data.get("rejected", 0)
        total_accepted += accepted
        total_rejected += rejected
        daily.append({
            "date":     data.get("date", doc.id),
            "accepted": accepted,
            "rejected": rejected,
        })

    total = total_accepted + total_rejected
    rejection_rate = round(total_rejected / total, 3) if total > 0 else 0.0
    monthly_total = sum(d["accepted"] + d["rejected"] for d in daily[:30])

    return {
        "total_accepted":  total_accepted,
        "total_rejected":  total_rejected,
        "rejection_rate":  rejection_rate,
        "monthly_total":   monthly_total,
        "phase2_trigger":  rejection_rate >= 0.5,
        "phase2_reasons":  _build_phase2_reasons(rejection_rate, monthly_total),
        "daily":           daily,
    }


def _increment_feedback_stats(db, decision: str) -> None:
    """
    日次の承諾/棄却カウントを Firestore に集計する。
    ドキュメントIDは YYYY-MM-DD（UTC）で自動生成。
    失敗しても feedback 自体の処理を妨げない。
    """
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats_ref = db.collection("review_stats").document(today)
        field = "accepted" if decision == "accept" else "rejected"
        stats_ref.set(
            {"date": today, field: firestore.Increment(1)},
            merge=True,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("棄却率集計の書き込みに失敗しました: %s", e)


def _build_phase2_reasons(rejection_rate: float, monthly_total: int) -> list[str]:
    """Phase 2 移行推奨の理由を列挙する"""
    reasons = []
    if rejection_rate >= 0.5:
        reasons.append(f"棄却率が {rejection_rate * 100:.0f}% に達しています（閾値: 50%）")
    if monthly_total >= 10:
        reasons.append(f"月次指摘数が {monthly_total} 件に達しています（閾値: 10件）")
    return reasons


def _extract_summary_from_items(items) -> str:
    """指摘件数から簡易サマリーを生成する（Gemini サマリーの代替）"""
    if not items:
        return "指摘事項はありませんでした。"
    yoconfirm = sum(1 for i in items if i.severity == "要確認")
    ai_shiteki = len(items) - yoconfirm
    parts = []
    if yoconfirm:
        parts.append(f"要確認: {yoconfirm}件")
    if ai_shiteki:
        parts.append(f"AIからの指摘: {ai_shiteki}件")
    return f"合計 {len(items)} 件の指摘があります（{', '.join(parts)}）。"
