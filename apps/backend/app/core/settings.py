"""
アプリケーション設定・パス定数

ファイルパスや環境設定はここで一元管理する。
各モジュールはここからインポートする。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── データディレクトリ ──────────────────────────
_BASE = Path("data/form_generation")

OUTPUT_DIR    = _BASE / "output"
UPLOAD_DIR    = _BASE / "input" / "uploaded"
CACHE_DIR     = _BASE / "cache"
TEMPLATE_PATH = _BASE / "input" / "templates" / "frameB_MRC.xlsx"

FRAMES_DIR = Path("frames")

# ── GCP ───────────────────────────────────────
GCP_PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
GCP_LOCATION   = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "")

# Vertex AI Search データストアID・エンジンID
# create_datastores.py で作成後に .env へ追記する
VERTEX_SEARCH_F2_DATASTORE_ID = os.environ.get("VERTEX_SEARCH_F2_DATASTORE_ID", "")
VERTEX_SEARCH_F3_DATASTORE_ID = os.environ.get("VERTEX_SEARCH_F3_DATASTORE_ID", "")
VERTEX_SEARCH_F2_ENGINE_ID    = os.environ.get("VERTEX_SEARCH_F2_ENGINE_ID", "")
VERTEX_SEARCH_F3_ENGINE_ID    = os.environ.get("VERTEX_SEARCH_F3_ENGINE_ID", "")
VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID = os.environ.get("VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID", "")
VERTEX_SEARCH_SUPPLEMENT_ENGINE_ID    = os.environ.get("VERTEX_SEARCH_SUPPLEMENT_ENGINE_ID", "")

# ── BigQuery（F3知識のデータ置き場・REQUIREMENTS §0-7 / RAG_VERIFICATION §3-3）──
# Excel(正本) → 平坦化(ver5.3) → BigQuery → Agent Search索引 → RAG検索
# BigQuery 自体は検索しない（Agent Search がこのテーブルを索引する）
BIGQUERY_DATASET_ID  = os.environ.get("BIGQUERY_DATASET_ID", "nuro_knowledge")
BIGQUERY_F3_TABLE_ID = os.environ.get("BIGQUERY_F3_TABLE_ID", "f3_flat_ver53")
BIGQUERY_LOCATION    = os.environ.get("BIGQUERY_LOCATION", "asia-northeast1")

# ── CORS ──────────────────────────────────────
# 本番環境ではデプロイ先URLに変更する
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:5175",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://127.0.0.1:5175",
]
