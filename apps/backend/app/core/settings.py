"""アプリケーショングローバル設定・パス定数管理モジュール。

本モジュールは、システム全体の構成・インフラ設定・ファイルストレージパスを規定する
決定論的構成管理レイヤー（システム基盤）に位置付けられる。
`.env` ファイルおよび環境変数から定義をロードし、型安全な Python 定数としてシステム全体に提供する。

パイプライン上の位置付け：
    アプリ起動時、および各 API ルート、RAGエンジン、AI判定クライアント、帳票出力モジュールから
    インポートされ、すべてのパラメータ（タイムアウト、モデル名、ディレクトリパス、機能トグルなど）
    の唯一の真実のソース（Single Source of Truth）として機能する。

責務範囲：
    - ディレクトリパスの自動設定（Paths モジュールからの委譲）。
    - 各種外部 API（Document AI, Gemini, Vertex Search）の接続情報・パラメータ設定のロード。
    - 認証、セキュリティ、アップロード制限の設定。
    - 例外：本モジュール自体は値の動的バリデーションや、外部サービスとの疎通確認などは行わない。

Args:
    なし。モジュールロード時に環境変数および `.env` から暗黙的にロードされる。

Returns:
    なし（定数定義モジュール）。主要な定数：
    - `OUTPUT_DIR` (Path): Excel 成果物の出力先ディレクトリ。
    - `UPLOAD_DIR` (Path): ユーザーからアップロードされた一時ファイルの保存先。
    - `CACHE_DIR` (Path): セルマッピング判定キャッシュの保存先。
    - `GEMINI_MODEL` (str): Gemini API にて利用するモデル識別子。
    - `AUTH_MODE` (str): `"local"` または `"iap"` の認証モード設定。
    - `RULE_CANDIDATE_CAPTURE_ENABLED` (bool): ルール候補キャプチャ機能の有効化状態。

Failure Behavior:
    - `.env` が存在しない場合は、OS の環境変数から直接ロードを試みる（`load_dotenv` は失敗せずにスキップされる）。
    - 必須ではない変数（例：`VERTEX_SEARCH_*`）が不足している場合、空文字列（`""`）等のデフォルト値が設定され
      該当機能へのアクセス時に初めてエラーとなる（遅延フェイルファスト）。
    - 整数変換（`int(os.environ.get(...))`）対象の変数に数値以外が設定されていた場合、
      モジュールロード時（起動時）に `ValueError` が送出され、アプリケーションは起動に失敗する。

Examples:
    >>> from apps.backend.app.core.settings import GEMINI_MODEL, GCP_PROJECT_ID  # doctest: +SKIP
    >>> print(GEMINI_MODEL)  # doctest: +SKIP
    'gemini-3.5-flash'

Note:
    - ローカル開発や CI、および本番環境（Cloud Run）の差分は、コンテナ起動時の環境変数インジェクションによって吸収される。
    - 一部のトグル定数（`DOCUMENT_AI_ENABLED` 等）は、文字列比較により小文字化した上で boolean に変換している。
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from apps.backend.app.config.paths import (
    FRAMES_CONFIG_ROOT,
    form_generation_cache_dir,
    form_generation_output_dir,
    rule_candidate_profile_path,
    template_workbook_path,
    upload_dir,
)

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

# ── データディレクトリ ─────────────────────────
OUTPUT_DIR      = form_generation_output_dir()
UPLOAD_DIR      = upload_dir()
CACHE_DIR       = form_generation_cache_dir()
TEMPLATE_PATH   = template_workbook_path()

FRAMES_DIR = FRAMES_CONFIG_ROOT

# ── GCP ─────────────────────────
GCP_PROJECT_ID  = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
GCP_LOCATION    = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "")

# ── Document AI ─────────────────────────
DOCUMENT_AI_PROCESSOR_LOCATION      = os.environ.get("DOCUMENT_AI_PROCESSOR_LOCATION", "us")
DOCUMENT_AI_PROCESSOR_ID            = os.environ.get("DOCUMENT_AI_PROCESSOR_ID", "")
DOCUMENT_AI_PROCESSOR_TYPE          = os.environ.get("DOCUMENT_AI_PROCESSOR_TYPE", "FORM_PARSER_PROCESSOR")
DOCUMENT_AI_ENABLED                 = os.environ.get("DOCUMENT_AI_ENABLED", "false").lower() == "true"
DOCUMENT_AI_API_TIMEOUT_SEC         = int(os.environ.get("DOCUMENT_AI_API_TIMEOUT_SEC", "120"))
DOCUMENT_AI_NORMALIZE_MAX_WORKERS   = int(os.environ.get("DOCUMENT_AI_NORMALIZE_MAX_WORKERS", "4"))
DOCUMENT_AI_TABLE_CHUNK_ROWS        = int(os.environ.get("DOCUMENT_AI_TABLE_CHUNK_ROWS", "8"))
DOCUMENT_AI_SCALAR_EXTRACTION_ENABLED = os.environ.get("DOCUMENT_AI_SCALAR_EXTRACTION_ENABLED", "true").lower() == "true"

# local: PoC/ローカル開発用に x-nuro-tenant-id を許可。
# iap: IAP/OIDC 等で検証済みの Google email ヘッダーから tenant を解決。
AUTH_MODE = os.environ.get("NURO_AUTH_MODE", "local").lower()

# ── Gemini ─────────────────────────
# デフォルトはプロジェクト標準 of 3.5 Flash。必要に応じて .env の GEMINI_MODEL で差し替える。
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_MAX_OUTPUT_TOKENS = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", 65_535))
GEMINI_API_TIMEOUT_SEC = int(os.environ.get("GEMINI_API_TIMEOUT_SEC", 600))
# テキストのみ経路（xlsx・docx 等）で Gemini に渡す text_content の最大文字数。
# この文字数を超える場合はチャンク分割して複数回呼び出し、結果をマージする。
# 目安: 20,000文字 ≈ 5,000 入力トークン。MAX_TOKENS 再試行を減らすため、既定値を下げる。
GEMINI_TEXT_CONTENT_MAX_CHARS = int(os.environ.get("GEMINI_TEXT_CONTENT_MAX_CHARS", 20_000))
GEMINI_INLINE_PDF_MAX_BYTES = int(os.environ.get("GEMINI_INLINE_PDF_MAX_BYTES", str(20 * 1024 * 1024)))
GEMINI_PDF_MAX_BYTES = int(os.environ.get("GEMINI_PDF_MAX_BYTES", str(50 * 1024 * 1024)))
GEMINI_TRANSFER_LANGFUSE_ENABLED = os.environ.get("GEMINI_TRANSFER_LANGFUSE_ENABLED", "false").lower() == "true"

# ── HITL後の再計算フラグ ─────────────────────────
# Phase 0: まずは設定だけ追加し、既存挙動は変えない。
RECOMPUTE_ON_ANSWER_ENABLED = os.environ.get("RECOMPUTE_ON_ANSWER_ENABLED", "false").lower() == "true"

# ── PDF generic extraction tuning ─────────────────────────
# 0 を指定すると scalar フィールドは従来通り 1 グループにまとめる。
PDF_GENERIC_SCALAR_FIELDS_PER_GROUP = int(os.environ.get("PDF_GENERIC_SCALAR_FIELDS_PER_GROUP", "0"))
# 0 以下は自動（グループ数と4の小さい方）
PDF_GENERIC_GROUP_MAX_WORKERS = int(os.environ.get("PDF_GENERIC_GROUP_MAX_WORKERS", "0"))
PDF_GENERIC_LIST_MAX_CONTINUATIONS = int(os.environ.get("PDF_GENERIC_LIST_MAX_CONTINUATIONS", "2"))

# ── アップロード安全弁 ─────────────────────────
MAX_TRANSCRIBE_FILES   = int(os.environ.get("MAX_TRANSCRIBE_FILES", "10"))
MAX_UPLOAD_FILE_BYTES  = int(os.environ.get("MAX_UPLOAD_FILE_BYTES", str(50 * 1024 * 1024)))
MAX_UPLOAD_TOTAL_BYTES = int(os.environ.get("MAX_UPLOAD_TOTAL_BYTES", str(150 * 1024 * 1024)))

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

# ── BigQuery（F2/F3知識のデータ置き場・REQUIREMENTS §0-7 / RAG_VERIFICATION §3-3） ─────────────────────────
# Excel（正本）→ 平坦化(ver5.3) → BigQuery → Agent Search索引 → RAG検索
# BigQuery 自体は検索しない（Agent Search がこのテーブルを索引する）
BIGQUERY_DATASET_ID = os.environ.get("BIGQUERY_DATASET_ID", "nuro_knowledge")
BIGQUERY_F3_TABLE_ID = os.environ.get("BIGQUERY_F3_TABLE_ID", "f3_flat_ver53")
BIGQUERY_F2_TABLE_ID = os.environ.get("BIGQUERY_F2_TABLE_ID", "f2_flat_ver53")
BIGQUERY_LOCATION    = os.environ.get("BIGQUERY_LOCATION", "asia-northeast1")

# ── CORS ─────────────────────────
# 本番環境ではデプロイ先URLに変更する
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:5175",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://127.0.0.1:5175",
]

# ── ルール候補キャプチャ（PoC） ─────────────────────────
RULE_CANDIDATE_CAPTURE_ENABLED = os.environ.get("RULE_CANDIDATE_CAPTURE_ENABLED", "false").lower() == "true"
RULE_CANDIDATES_PATH = os.environ.get(
    "RULE_CANDIDATES_PATH",
    str(rule_candidate_profile_path()),
)