"""
アプリケーション設定・パス定数

ファイルパスや環境設定はここで一元管理する。
各モジュールはここからインポートする。
"""
from pathlib import Path

# ── データディレクトリ ──────────────────────────
_BASE = Path("data/form_generation")

OUTPUT_DIR  = _BASE / "output"
UPLOAD_DIR  = _BASE / "input" / "uploaded"
CACHE_DIR   = _BASE / "cache"
TEMPLATE_PATH = _BASE / "input" / "templates" / "frameB_MRC.xlsx"

FRAMES_DIR  = Path("frames")

# ── CORS ──────────────────────────────────────
# 本番環境ではデプロイ先URLに変更する
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
