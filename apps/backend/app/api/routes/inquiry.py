"""問い合わせナレッジ対応エンドポイント（docs/inquiry/DESIGN.md §4-1）

POST /api/inquiry/ask - (a) 質問→3段パイプライン→引用付き回答 or 棄却

起票管理（POST/GET /api/inquiry ほか）はフェーズ2、AIドラフト（/draft）はフェーズ3。

エラー方針（DESIGN §6）：
    棄却（abstained）は正常系＝200 で返し、起票導線に流す。
    ①検索の障害（KnowledgeSearchError）は 502＝「ナレッジなし」と誤認させない
    （偽の棄却は評価指標と起票品質を汚す）。②③④の障害は pipeline 内で棄却に倒す。

PoC の認証方針：
    utility はリクエストで受け取る（既存プラットフォームと同等の簡易運用・REQUIREMENTS §9-2）。
    本番移行時は user=Depends(get_current_user) から取得に差し替える。
"""
import logging

from fastapi import APIRouter, HTTPException

from apps.backend.app.inquiry.models import AskRequest, AskResult
from apps.backend.app.inquiry.pipeline import ask
from apps.backend.app.preliminary_review.knowledge.knowledge_loader import (
    KnowledgeSearchError,
)

logger = logging.getLogger(__name__)

router = APIRouter()


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
