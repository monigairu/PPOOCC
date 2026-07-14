"""問い合わせナレッジ対応エンドポイント（docs/inquiry/DESIGN.md §4-1）

(a) 自己解決:
    POST /api/inquiry/ask                - 質問→3段パイプライン→引用付き回答 or 棄却
(b) 起票管理（フェーズ2）:
    POST  /api/inquiry                   - 起票（棄却時の self_solve_log を添付可）
    GET   /api/inquiry?requester=        - 一覧（電力=自分の分／NuRO=全件）
    GET   /api/inquiry/{id}              - 詳細
    POST  /api/inquiry/{id}/answer       - NuRO回答登録（open→answered）
    PATCH /api/inquiry/{id}/status       - 電力側遷移（answered→resolved／answered→open・D-15）
(c) AIドラフト（フェーズ3）:
    POST  /api/inquiry/{id}/draft        - 保存済み content＋utility で ask() を再実行し
                                           ai_draft に保存（再生成で上書き・D-17）

エラー方針（DESIGN §6）：
    棄却（abstained）は正常系＝200 で返し、起票導線に流す。
    ①検索の障害（KnowledgeSearchError）は 502＝「ナレッジなし」と誤認させない
    （偽の棄却は評価指標と起票品質を汚す）。②③④の障害は pipeline 内で棄却に倒す。
    Firestore 障害も 502（起票消失を隠さない）。存在しないID=404・遷移違反=409 は
    store.py の例外を _map_store_errors で変換する。

PoC の認証方針：
    utility / requester はリクエストで受け取る（既存プラットフォームと同等の簡易運用・
    REQUIREMENTS §9-2）。本番移行時は user=Depends(get_current_user) から取得に差し替える。
"""
import logging
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException
from google.api_core.exceptions import GoogleAPIError

from apps.backend.app.inquiry import store
from apps.backend.app.inquiry.models import (
    AnswerCreate,
    AskRequest,
    AskResult,
    Inquiry,
    InquiryCreate,
    InquiryCreated,
    StatusUpdate,
)
from apps.backend.app.inquiry.pipeline import ask
from apps.backend.app.preliminary_review.knowledge.knowledge_loader import (
    KnowledgeSearchError,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@contextmanager
def _map_store_errors():
    """store.py の例外を HTTP ステータスへ変換する（DESIGN §6・D-15）。"""
    try:
        yield
    except store.InquiryNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"問い合わせが見つかりません: {e}")
    except store.InvalidTransitionError as e:
        raise HTTPException(
            status_code=409,
            detail=f"現在の状態（{e.current}）からは操作できません（要求: {e.requested}）。"
            "画面を再読み込みして最新の状態を確認してください。",
        )
    except GoogleAPIError as e:
        logger.error("Firestore 障害のため 502 で返す: %s", e)
        raise HTTPException(
            status_code=502,
            detail="問い合わせデータベースにアクセスできませんでした。時間をおいて再度お試しください。",
        )


# ── (a) 自己解決 ─────────────────────────────────────────────────────────────

@router.post("/inquiry/ask", response_model=AskResult)
def ask_inquiry(request: AskRequest) -> AskResult:
    """質問に対しF3自社ナレッジから引用付き回答（または棄却）を返す。

    同期 def のため FastAPI がスレッドプールで実行する（pipeline はブロッキングI/O）。
    """
    try:
        return ask(request.question, request.utility)
    except KnowledgeSearchError as e:
        logger.error("ナレッジ検索障害のため /ask を 502 で返す: %s", e)
        raise HTTPException(
            status_code=502,
            detail="ナレッジ検索が実行できませんでした。時間をおいて再度お試しください。",
        )


# ── (b) 起票管理 ─────────────────────────────────────────────────────────────

@router.post("/inquiry", response_model=InquiryCreated, status_code=201)
def create_inquiry(request: InquiryCreate) -> InquiryCreated:
    """起票する。棄却→起票導線では self_solve_log（直前の AskResult）を添付する（§4-2）。"""
    with _map_store_errors():
        saved = store.create_inquiry(request)
    return InquiryCreated(inquiry_id=saved.inquiry_id, number=saved.number)


@router.get("/inquiry", response_model=list[Inquiry])
def list_inquiries(requester: str | None = None) -> list[Inquiry]:
    """一覧（updated_at 降順）。requester 指定=自分の分のみ（電力）／無指定=全件（NuRO）。"""
    with _map_store_errors():
        return store.list_inquiries(requester=requester)


@router.get("/inquiry/{inquiry_id}", response_model=Inquiry)
def get_inquiry(inquiry_id: str) -> Inquiry:
    """詳細1件。存在しなければ 404。"""
    with _map_store_errors():
        return store.get_inquiry(inquiry_id)


@router.post("/inquiry/{inquiry_id}/answer", status_code=204)
def answer_inquiry(inquiry_id: str, request: AnswerCreate) -> None:
    """NuRO回答を登録する（open→answered。open 以外は 409）。"""
    with _map_store_errors():
        store.save_answer(inquiry_id, request)


@router.patch("/inquiry/{inquiry_id}/status", status_code=204)
def update_inquiry_status(inquiry_id: str, request: StatusUpdate) -> None:
    """電力側の状態遷移（answered→resolved=解決確認／answered→open=差し戻し）。

    open→answered は /answer 専用（回答なしの answered を作らせない・D-15）。
    """
    with _map_store_errors():
        store.update_status(inquiry_id, request.status)


# ── (c) AIドラフト ───────────────────────────────────────────────────────────

@router.post("/inquiry/{inquiry_id}/draft", response_model=AskResult)
def generate_draft(inquiry_id: str) -> AskResult:
    """保存済みの問い合わせ内容で ask() を再実行し、AIドラフトとして保存・返却する。

    棄却ドラフトも 200 の正常系（related の近傍ナレッジが NuRO の参考情報・§3-3）。
    utility 未保存の文書（D-17 以前の起票）は 409＝再起票を促す。
    """
    with _map_store_errors():
        inquiry = store.get_inquiry(inquiry_id)
    if inquiry.utility is None:
        raise HTTPException(
            status_code=409,
            detail="起票時の電力会社情報が無いためドラフトを生成できません。"
            "お手数ですが質問画面から再起票してください。",
        )
    try:
        draft = ask(inquiry.content, inquiry.utility)
    except KnowledgeSearchError as e:
        logger.error("ナレッジ検索障害のため /draft を 502 で返す: %s", e)
        raise HTTPException(
            status_code=502,
            detail="ナレッジ検索が実行できませんでした。時間をおいて再度お試しください。",
        )
    with _map_store_errors():
        store.save_draft(inquiry_id, draft)
    return draft
