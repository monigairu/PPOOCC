"""
APIのリクエスト・レスポンスの型定義

FastAPIはここで定義した型を使って
自動的にバリデーション・ドキュメント生成をしてくれる
"""
from pydantic import BaseModel


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


class ChatEditRequest(BaseModel):
    """セル編集チャットのリクエスト"""
    session_id: str                  # 編集対象のセッション ID
    message: str                     # ユーザーの自然言語による編集指示
    sheet_name: str = "MRC1"
    frame_name: str = "frameB"


class EditedCell(BaseModel):
    """1フィールド分の編集結果"""
    field_name: str                  # 変更したフィールド名
    cell_addresses: list[str]        # 書き込んだセル番地のリスト（複数の場合あり）
    new_value: str                   # 書き込んだ値


class ChatEditResponse(BaseModel):
    """セル編集チャットのレスポンス"""
    status: str                              # "edited" | "ambiguous" | "not_edit" | "field_not_found"
    message: str                             # ユーザー向けの自然言語メッセージ
    edited_cells: list[EditedCell] | None = None  # 編集したセル情報（status=="edited" 時）


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

class ReviewItem(BaseModel):
    """1件の指摘事項"""
    item_id: str            # 指摘ID（例: "review_001"）
    field_name: str         # 対象フィールド名（例: "費用低減策"）
    cell_address: str       # 対象セル番地（例: "K22"）
    severity: str           # "要確認" or "AIからの指摘"
    comment: str            # 指摘内容（自然言語）
    evidence: str           # 根拠（ナレッジのIDや内容）
    knowledge_source: str   # "F2" / "F3" / "計画差分"


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
