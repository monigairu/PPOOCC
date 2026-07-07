"""事前レビュー（RAG）固有の設定モジュール。

Vertex AI Search・Reranking・BigQuery（F2/F3ナレッジ置き場）など、
事前レビュー機能だけが参照する設定を集約する。
アプリ全体で共有する設定（GCP プロジェクト・Gemini・ディレクトリパス等）は
従来どおり `apps.backend.app.core.settings` が唯一の真実のソース。

`.env` からのロード・環境変数パースの流儀は core.settings と同一
（load_dotenv は settings 側の import 時に実行済み）。
"""
import os

from apps.backend.app.core.settings import _env_bool, _env_float

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
