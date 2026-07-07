"""
APIのリクエスト・レスポンスの型定義

FastAPIはここで定義した型を使って
自動的にバリデーション・ドキュメント生成をしてくれる
"""
from pydantic import BaseModel

# ReviewItem の定義本体は事前レビュー側（preliminary_review/models.py）。
# API のレスポンス型（ReviewResponse 等）として使うため、ここから re-export する（既存 import 互換）。
from apps.backend.app.preliminary_review.models import ReviewItem


# ── チャット関連 ──────────────────────────────

class ChatRequest(BaseModel):
    """統合チャットのリクエスト（Q&A + セル編集を一本化）"""
    session_id: str                  # 編集操作に必要なセッション ID
    message: str                     # ユーザーのメッセージ
    cell_address: str = ""           # 選択中のセル番地（Q&A コンテキスト用）
    field_name: str = ""             # 選択中のフィールド名
    field_value: str = ""            # 選択中のセルの現在値
    reasoning: str = ""              # 転記時の根拠
    sheet_name: str = "MRC1"
    frame_name: str = "frameB"


class ChatResponse(BaseModel):
    """統合チャットのレスポンス"""
    type: str                                    # "answer" | "edited" | "ambiguous"
    answer: str                                  # 常に自然言語メッセージを含む
    edited_cells: list["EditedCell"] | None = None  # type=="edited" 時のみ


class EditedCell(BaseModel):
    """1フィールド分の編集結果"""
    field_name: str                  # 変更したフィールド名
    cell_addresses: list[str]        # 書き込んだセル番地のリスト（複数の場合あり）
    new_value: str                   # 書き込んだ値


# ── 転記結果関連 ──────────────────────────────

class CellMapping(BaseModel):
    """セル1つ分のマッピング情報"""
    field_name: str        # フィールド名（例: "炉型"）
    cell_address: str      # セル番地（例: "C7"）
    value: str             # 書き込んだ値（例: "PWR"）
    reasoning: str         # AIの根拠説明


class UploadResponse(BaseModel):
    """アップロード・転記実行後のレスポンス"""
    session_id: str              # セッションID（ダウンロード時に使う）
    frame_name: str              # 様式名（例: "frameB"）ダウンロードURLに使用
    sheet_name: str              # 処理した主シート名
    mappings: list[CellMapping]  # セルごとのマッピング情報一覧
    message: str                 # 処理結果メッセージ


class DownloadResponse(BaseModel):
    """ダウンロード用のレスポンス"""
    file_path: str         # ダウンロードできるファイルパス


# ── レビュー関連 ──────────────────────────────

class ReviewRequest(BaseModel):
    """レビュー実行リクエスト"""
    session_id: str         # FirestoreのセッションID（転記完了済み）
    utility_name: str       # 電力会社名（ナレッジフィルタリング用）
    sheet_name: str = "MRC1"
    frame_name: str = "frameB"


class ReviewResponse(BaseModel):
    """レビュー結果レスポンス"""
    review_id: str
    review_items: list[ReviewItem]
    summary: str
    reviewed_at: str
    mappings: list[dict] = []           # 転記結果（グリッド表示用）
    retrieval_trace: list[dict] = []    # 各ToolのRAG取得ログ（RAG詳細パネル用）
    feedbacks: list[dict] = []          # 承諾/棄却フィードバック（復元用。新規実行時は空）


class MultiSheetReviewResponse(BaseModel):
    """複数シート一括レビューのレスポンス（例: MRC1・MRC2を1回のリクエストでレビュー）"""
    sheets: dict[str, ReviewResponse]   # {"MRC1": ReviewResponse, "MRC2": ReviewResponse, ...}
    skipped_sheets: list[str] = []      # 転記データが無くレビューできなかったシート名


class FeedbackRequest(BaseModel):
    """NuROによる指摘への承諾/棄却リクエスト"""
    item_id: str
    decision: str           # "accept"（承諾）or "reject"（棄却）
    comment: str = ""       # NuROのコメント（任意）
    session_id: str = ""    # 直接パス取得用（collection_groupインデックス不要）


class FeedbackResponse(BaseModel):
    """承諾/棄却後のレスポンス"""
    status: str             # "saved"（Firestore保存）or "discarded"（破棄）


class FeedbackSyncRequest(BaseModel):
    """保存ボタン押下時の一括フィードバック同期リクエスト"""
    feedbacks: list[dict]   # [{"item_id": "...", "decision": "accept"|"reject"}, ...]
    session_id: str = ""    # 直接パス取得用


class SessionSummary(BaseModel):
    """NuRO画面のセッション一覧表示用"""
    session_id: str
    utility_name: str
    session_name: str = ""   # 転記時に工事件名などから自動生成
    frame_name: str
    sheet_name: str
    created_at: str
    reviewed: bool
