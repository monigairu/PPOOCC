"""
アプリケーション設定・パス定数

ファイルパスや環境設定はここで一元管理する。
各モジュールはここからインポートする。
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from apps.backend.app.config.path import template_workbook_path

load_dotenv()

logger = logging.getLogger(__name__)


def _env_bool(key: str, default: bool) -> bool:
    """環境変数を真偽値として読む（"1"/"true"/"yes"/"on" を真として許容）。

    Args:
        key: 環境変数名。
        default: 未設定時に返す値。

    Returns:
        真値集合に一致すれば True、明示的な偽値なら False、未設定なら default。
    """
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(key: str, default: float) -> float:
    """環境変数を float として読む（不正値はクラッシュさせず default に退避）。

    設定ファイル import 時に落ちるとアプリ全体（様式作成含む）が起動不能になるため、
    パースできない値は警告を出して default を用いる。

    Args:
        key: 環境変数名。
        default: 未設定・不正時に返す値。

    Returns:
        パースできた float、または default。
    """
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("環境変数 %s=%r を float として解釈できません。既定値 %s を使用します", key, raw, default)
        return default

# ── データディレクトリ ──────────────────────────
_BASE = Path("data/form_generation")

OUTPUT_DIR    = _BASE / "output"
UPLOAD_DIR    = _BASE / "input" / "uploaded"
CACHE_DIR     = _BASE / "cache"
TEMPLATE_PATH = template_workbook_path()

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

# BigQuery平坦テーブルを索引する構造化データストア（§0-7 R3・Step1）
VERTEX_SEARCH_F3_BQ_DATASTORE_ID = os.environ.get("VERTEX_SEARCH_F3_BQ_DATASTORE_ID", "")
VERTEX_SEARCH_F3_BQ_ENGINE_ID    = os.environ.get("VERTEX_SEARCH_F3_BQ_ENGINE_ID", "")
VERTEX_SEARCH_F2_BQ_DATASTORE_ID = os.environ.get("VERTEX_SEARCH_F2_BQ_DATASTORE_ID", "")
VERTEX_SEARCH_F2_BQ_ENGINE_ID    = os.environ.get("VERTEX_SEARCH_F2_BQ_ENGINE_ID", "")

# ── Reranking（Agent Search Ranking API・§3-2 採用方針／Step5）──────────────
# ハイブリッド検索の結果を semantic-ranker で関連度順に並べ替え、surfacing を底上げする。
# _search() 後段に適用（I/F不変）。RankService は google-cloud-discoveryengine に同梱。
RERANK_ENABLED = _env_bool("RERANK_ENABLED", True)
# 再現性のため @latest でなくバージョンをピン留め（-004＝25言語対応・1024トークン・2025-04 GA）
RERANK_MODEL   = os.environ.get("RERANK_MODEL", "semantic-ranker-default-004")
# F2（費目非保持ナレッジ）の関連性ガード判定に使うスコア閾値（§1-18 の正式方針）。
# PoC実データで校正：semantic-ranker は費目クエリに対し F3=0.20〜0.36（費目特化で強関連）／
# F2=0.04〜0.08（NuRO内知見ゆえ弱関連）と2.5倍のギャップで分離する。この gap の中間 0.15 に置くと、
# F2は「意味的に強関連なとき（例：主題一致で0.37）」のみ根拠採用され、字面2文字の偽陽性
# （放射線管理⇔放射性が0.05）は確実に不採用になる。件数規模が変わったら env で再校正する。
RERANK_GUARD_F2_THRESHOLD = _env_float("RERANK_GUARD_F2_THRESHOLD", 0.15)

# ── BigQuery（F2/F3知識のデータ置き場・REQUIREMENTS §0-7 / RAG_VERIFICATION §3-3）──
# Excel(正本) → 平坦化(ver5.3) → BigQuery → Agent Search索引 → RAG検索
# BigQuery 自体は検索しない（Agent Search がこのテーブルを索引する）
BIGQUERY_DATASET_ID  = os.environ.get("BIGQUERY_DATASET_ID", "nuro_knowledge")
BIGQUERY_F3_TABLE_ID = os.environ.get("BIGQUERY_F3_TABLE_ID", "f3_flat_ver53")
BIGQUERY_F2_TABLE_ID = os.environ.get("BIGQUERY_F2_TABLE_ID", "f2_flat_ver53")
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
