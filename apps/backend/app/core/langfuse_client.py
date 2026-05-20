"""
Langfuse クライアント（RAGトレーシング・エンジニア向け）

LANGFUSE_PUBLIC_KEY と LANGFUSE_SECRET_KEY が .env に設定されている場合のみ有効。
未設定の場合は全操作がノーオペレーションになり、アプリの動作に影響しない。

起動方法:
    docker compose -f docker-compose.langfuse.yml up -d
    → http://localhost:3000 でUI確認
    → Settings → API Keys でキーを発行して .env に記入

トレース内容:
    - 各Toolの検索クエリ・取得件数・代表ドキュメントID
    - Geminiへ渡したプロンプトの長さ・出力の指摘件数
    - 承諾/棄却フィードバック（routes/review.py から呼び出し）
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_langfuse: Any | None = None
_initialized = False


def is_langfuse_enabled() -> bool:
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
    )


def get_langfuse() -> Any | None:
    """Langfuse クライアントを返す。未設定ならNone。"""
    global _langfuse, _initialized
    if _initialized:
        return _langfuse
    _initialized = True

    if not is_langfuse_enabled():
        logger.debug("Langfuse: キー未設定のためトレーシング無効")
        return None

    try:
        from langfuse import Langfuse
        _langfuse = Langfuse(
            public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
            host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000"),
            flush_at=1,  # 開発環境: 即座に送信（本番では削除推奨）
            flush_interval=0.1,  # 100ms ごとにフラッシュ
        )
        logger.info("Langfuse: トレーシング有効 (%s)", os.environ.get("LANGFUSE_HOST"))
    except Exception as e:
        logger.warning("Langfuse: 初期化に失敗しました: %s", e)
        _langfuse = None

    return _langfuse
