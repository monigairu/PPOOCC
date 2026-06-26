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
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from google.cloud import firestore

from apps.backend.app.agents.reviewer import reviewer_agent
from apps.backend.app.agents.reviewer.result_reader import reconstruct_mappings_from_excel
from apps.backend.app.api.models import (
    CellMapping,
    FeedbackRequest,
    FeedbackResponse,
    FeedbackSyncRequest,
    ReviewRequest,
    ReviewResponse,
    SessionSummary,
    UploadResponse,
)
from apps.backend.app.core.firestore_client import get_firestore_client
from apps.backend.app.core.settings import OUTPUT_DIR

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_review_result_doc(db, review_id: str, session_id: str = ""):
    """review_results ドキュメントを取得する。
    session_id があれば直接パスで取得（Firestoreインデックス不要）。
    """
    if session_id:
        ref = (
            db.collection("sessions")
            .document(session_id)
            .collection("review_results")
            .document(review_id)
        )
        doc = ref.get()
        return doc if doc.exists else None
    # fallback: collection_group（インデックスが必要なため非推奨）
    docs = (
        db.collection_group("review_results")
        .where(filter=firestore.FieldFilter("review_id", "==", review_id))
        .limit(1)
        .stream()
    )
    return next(docs, None)


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

    # AIレビュー実行（Agentic RAG: 5 Tool 固定順実行）
    try:
        review_items, retrieval_trace = await reviewer_agent.run_review(
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
    summary = _extract_summary_from_items(review_items)

    # レビュー結果をFirestoreのサブコレクションに保存
    # retrieval_trace はデバッグ用途のため Firestore には保存しない
    review_ref = session_ref.collection("review_results").document(review_id)
    review_ref.set({
        "review_id": review_id,
        "review_items": [item.model_dump() for item in review_items],
        "summary": summary,
        "reviewed_at": reviewed_at,
        "feedbacks": [],
        "total_count": len(review_items),
        "decided_count": 0,
    })

    session_ref.update({"reviewed": True})

    return ReviewResponse(
        review_id=review_id,
        review_items=review_items,
        summary=summary,
        reviewed_at=reviewed_at,
        mappings=mappings,
        retrieval_trace=retrieval_trace,
    )


@router.post("/review/upload", response_model=UploadResponse)
async def upload_form_for_review(
    file: UploadFile = File(...),
    frame_name: str = Form(default="frameB", pattern=r"^[a-zA-Z0-9_\-]+$"),
    sheet_name: str = Form(default="MRC1", pattern=r"^[a-zA-Z0-9_\-]+$"),
    utility_name: str = Form(default=""),
):
    """完成した様式（転記結果Excel）を受け取り、レビュー対象セッションを作成する。

    様式自動作成（転記）を経ずに、すでに転記済みの様式Excelをそのまま事前レビューに
    かけるための入口。Excel→mappings 復元（result_reader）→ Firestore セッション保存まで行い、
    以降は既存の /review 画面（セッション選択 → POST /api/review）でレビューできる。

    PoC: caller_role="NuRO" 固定（レビューは reviewer_agent.run_review が担当）。
    """
    filename = file.filename or "form.xlsx"
    if Path(filename).suffix.lower() not in (".xlsx", ".xls"):
        raise HTTPException(status_code=400, detail="様式は .xlsx / .xls を指定してください")

    safe_frame = Path(frame_name).name
    safe_sheet = Path(sheet_name).name
    session_id = str(uuid.uuid4())

    # result-layout エンドポイントが参照する命名で保存する（中央プレビューを既存経路で描画）
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result_path = OUTPUT_DIR / f"result_{safe_frame}_{session_id}.xlsx"
    try:
        result_path.write_bytes(await file.read())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"様式の保存に失敗しました: {e}")

    # 様式定義（config）に基づき mappings を復元（特定費目・特定ファイルに依存しない）
    try:
        mappings = reconstruct_mappings_from_excel(result_path, safe_frame, safe_sheet)
    except FileNotFoundError:
        result_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"様式定義が見つかりません: {safe_frame}/{safe_sheet}")
    except Exception as e:
        result_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"様式の読み込みに失敗しました: {e}")

    if not mappings:
        result_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="様式から転記内容を読み取れませんでした（シート名・様式を確認してください）")

    # 電力会社名は様式から自動取得（未指定時）。会社名はナレッジフィルタに使う
    resolved_utility = utility_name.strip() or reviewer_agent._extract_field(mappings, "電力会社") or "未設定"
    session_name = _extract_session_name(mappings, filename)

    _save_review_session(
        session_id=session_id,
        utility_name=resolved_utility,
        frame_name=safe_frame,
        sheet_name=safe_sheet,
        mappings=mappings,
        session_name=session_name,
    )

    return UploadResponse(
        session_id=session_id,
        frame_name=safe_frame,
        sheet_name=safe_sheet,
        mappings=[CellMapping(**m) for m in mappings],
        message=f"様式を読み込みました（{len(mappings)}項目）。レビューを開始できます。",
    )


def _extract_session_name(mappings: list[dict], filename: str) -> str:
    """様式から表示用セッション名を生成する（工事件名 > ファイル名）。"""
    for field in ("工事件名", "件名", "工事名"):
        for m in mappings:
            if m.get("field_name") == field:
                val = str(m.get("value", "")).strip()
                if val and val not in ("なし", "N/A", "-", ""):
                    return val[:40]
    return Path(filename).stem[:40]


def _save_review_session(
    session_id: str,
    utility_name: str,
    frame_name: str,
    sheet_name: str,
    mappings: list[dict],
    session_name: str = "",
) -> None:
    """レビュー対象セッションを Firestore に保存する（/api/sessions と互換のスキーマ）。"""
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
            "output_gcs_path": None,
        })
    except Exception as e:
        logger.warning("Firestoreへのレビューセッション保存に失敗（session_id=%s）: %s", session_id, e)


@router.post("/review/{review_id}/feedback", response_model=FeedbackResponse)
async def submit_feedback(review_id: str, request: FeedbackRequest):
    """
    NuROが指摘事項に対して承諾（accept）または棄却（reject）を行う。

    承諾/棄却どちらも feedbacks 配列に保存することで、履歴復元時に正確に再現できる。
    """
    if request.decision not in ("accept", "reject"):
        raise HTTPException(status_code=400, detail="decision は 'accept' または 'reject' を指定してください")

    db = get_firestore_client()
    review_doc = _get_review_result_doc(db, review_id, request.session_id)
    if review_doc is None:
        raise HTTPException(status_code=404, detail=f"レビュー結果が見つかりません: {review_id}")

    ts_key = "accepted_at" if request.decision == "accept" else "rejected_at"
    feedback_entry = {
        "item_id": request.item_id,
        "decision": request.decision,
        "comment": request.comment,
        ts_key: datetime.now(timezone.utc).isoformat(),
    }
    review_doc.reference.update({
        "feedbacks": firestore.ArrayUnion([feedback_entry]),
        "decided_count": firestore.Increment(1),
    })

    _increment_feedback_stats(db=db, decision=request.decision)
    _record_langfuse_feedback(review_id=review_id, item_id=request.item_id, decision=request.decision)

    status = "saved" if request.decision == "accept" else "discarded"
    return FeedbackResponse(status=status)


@router.get("/review/sessions", response_model=list[SessionSummary])
async def list_sessions(include_history: bool = Query(False)):
    """
    セッション一覧を返す（NuROの画面での選択用）。

    include_history=false（デフォルト）: 未レビューのセッションのみ
    include_history=true: レビュー済みを含む全セッション（履歴モード）

    PoC：全セッションを返す（認証なし）
    本番移行時：JWT から utility_name を取得してフィルタリングを追加する
    """
    db = get_firestore_client()

    query = db.collection("sessions")
    if not include_history:
        query = query.where(filter=firestore.FieldFilter("reviewed", "==", False))
    docs = (
        query
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(50)
        .stream()
    )

    sessions = []
    for doc in docs:
        data = doc.to_dict()
        created_at = data.get("created_at")
        if hasattr(created_at, "isoformat"):
            created_at_str = created_at.isoformat()
        else:
            created_at_str = str(created_at) if created_at else ""

        sessions.append(
            SessionSummary(
                session_id=data.get("session_id", doc.id),
                utility_name=data.get("utility_name", "未設定"),
                session_name=data.get("session_name", ""),
                frame_name=data.get("frame_name", ""),
                sheet_name=data.get("sheet_name", ""),
                created_at=created_at_str,
                reviewed=data.get("reviewed", False),
            )
        )

    return sessions


@router.get("/review/{session_id}/result")
async def get_latest_review_result(session_id: str):
    """
    セッションの最新レビュー結果を返す（ページ復元用）。

    ページを切り替えて戻ってきたとき、最後のレビュー結果を再表示するために使用する。
    レビュー結果が存在しない場合は 404 を返す。
    """
    db = get_firestore_client()
    session_ref = db.collection("sessions").document(session_id)

    result_docs = (
        session_ref.collection("review_results")
        .order_by("reviewed_at", direction=firestore.Query.DESCENDING)
        .limit(1)
        .stream()
    )
    result_doc = next(result_docs, None)
    if result_doc is None:
        raise HTTPException(status_code=404, detail="レビュー結果が見つかりません")

    data = result_doc.to_dict()
    session_doc = session_ref.get()
    mappings = session_doc.to_dict().get("mappings", []) if session_doc.exists else []

    return {
        "review_id": data.get("review_id", result_doc.id),
        "review_items": data.get("review_items", []),
        "summary": data.get("summary", ""),
        "reviewed_at": data.get("reviewed_at", ""),
        "mappings": mappings,
        "retrieval_trace": [],
        "feedbacks": data.get("feedbacks", []),
    }


@router.post("/review/{review_id}/feedbacks/sync")
async def sync_feedbacks(review_id: str, request: FeedbackSyncRequest):
    """
    保存ボタン押下時に現在のフィードバック全件を一括で上書き保存する。

    リアルタイム保存の漏れを補完するため、保存ボタン押下時に feedbacks 配列を
    現在の状態で完全に上書きする。decided_count も再計算する。
    """
    db = get_firestore_client()
    review_doc = _get_review_result_doc(db, review_id, request.session_id)
    if review_doc is None:
        raise HTTPException(status_code=404, detail=f"レビュー結果が見つかりません: {review_id}")

    now = datetime.now(timezone.utc).isoformat()
    feedbacks = []
    for f in request.feedbacks:
        decision = f.get("decision", "")
        if decision not in ("accept", "reject"):
            continue
        ts_key = "accepted_at" if decision == "accept" else "rejected_at"
        feedbacks.append({
            "item_id": f["item_id"],
            "decision": decision,
            "comment": f.get("comment", ""),
            ts_key: now,
        })

    review_doc.reference.update({
        "feedbacks": feedbacks,
        "decided_count": len(feedbacks),
    })
    return {"status": "synced", "count": len(feedbacks)}


@router.delete("/review/{review_id}/feedback/{item_id}")
async def undo_feedback(review_id: str, item_id: str, session_id: str = Query("")):
    """
    承諾または棄却を取り消して未決定状態に戻す。

    Firestoreの feedbacks 配列から該当 item_id のエントリを削除する。
    ※ review_stats（棄却率集計）の数値は遡及修正しない（近似値として許容）。
    """
    db = get_firestore_client()
    review_doc = _get_review_result_doc(db, review_id, session_id)

    if review_doc is not None:
        data = review_doc.to_dict()
        old_feedbacks = data.get("feedbacks", [])
        new_feedbacks = [f for f in old_feedbacks if f.get("item_id") != item_id]
        # 承諾/棄却どちらも feedbacks に保存されているため、削除と decided_count デクリメントを行う。
        review_doc.reference.update({
            "feedbacks": new_feedbacks,
            "decided_count": firestore.Increment(-1),
        })

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
        logger.warning("棄却率集計の書き込みに失敗しました: %s", e)


def _record_langfuse_feedback(review_id: str, item_id: str, decision: str) -> None:
    """Langfuse に承諾/棄却フィードバックをスコアとして記録する。"""
    try:
        from apps.backend.app.core.langfuse_client import get_langfuse
        lf = get_langfuse()
        if lf:
            lf.score(
                trace_id=review_id,
                name="feedback",
                value=1.0 if decision == "accept" else 0.0,
                comment=f"item_id={item_id}, decision={decision}",
            )
    except Exception as e:
        logger.debug("Langfuse フィードバック記録エラー（無視）: %s", e)


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
