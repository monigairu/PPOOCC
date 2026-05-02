"""
APIのリクエスト・レスポンスの型定義

FastAPIはここで定義した型を使って
自動的にバリデーション・ドキュメント生成をしてくれる
"""
from pydantic import BaseModel


# ── チャット関連 ──────────────────────────────

class ChatRequest(BaseModel):
    """チャットのリクエスト"""
    message: str           # ユーザーのメッセージ
    cell_address: str      # 質問対象のセル番地（例: "C7"）
    field_name: str        # フィールド名（例: "炉型"）
    field_value: str       # セルの値（例: "PWR"）
    reasoning: str         # AIが転記時に出した根拠


class ChatResponse(BaseModel):
    """チャットのレスポンス"""
    answer: str            # AIの回答


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
    sheet_name: str              # 処理したシート名
    mappings: list[CellMapping]  # セルごとのマッピング情報一覧
    message: str                 # 処理結果メッセージ


class DownloadResponse(BaseModel):
    """ダウンロード用のレスポンス"""
    file_path: str         # ダウンロードできるファイルパス
