"""事前レビュー（RAG）のドメインモデル。

レビュー1件分の指摘事項 `ReviewItem` を定義する。
API レスポンス（`apps.backend.app.api.models` の ReviewResponse 等）からも
参照されるため、API 層からは re-export される。
"""
from pydantic import BaseModel


class ReviewItem(BaseModel):
    """1件の指摘事項"""
    item_id: str            # 指摘ID（例: "review_001"）
    field_name: str         # 対象フィールド名（例: "費用低減策"）
    cell_address: str       # 対象セル番地（例: "K22"）
    severity: str           # "要確認" or "AIからの指摘"
    comment: str            # 指摘内容（自然言語）
    evidence: str           # 根拠（ナレッジのIDや内容）
    knowledge_source: str   # "F2" / "F3" / "計画差分"
