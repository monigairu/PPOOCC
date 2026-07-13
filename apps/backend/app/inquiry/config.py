"""問い合わせナレッジ対応（inquiry）固有の設定モジュール。

閾値・モデル名・k値を env から読み、デフォルトを持つ（DESIGN §5）。
「閾値・モデル・k値をコードに直書きしない」担保として本モジュールに集約する。

- アプリ全体で共有する設定は従来どおり `core.settings` が唯一の真実のソース。
- F3データストアの指定は既存の事前レビュー用 env を共用する（DESIGN §5）。
  検索自体は `preliminary_review/knowledge/knowledge_loader.load_f3()` を
  読み取りのみで再利用するため、本モジュールではデータストアIDを持たない。

`.env` からのロード・環境変数パースの流儀は core.settings と同一
（load_dotenv は settings 側の import 時に実行済み）。
"""
import os

from apps.backend.app.core.settings import GEMINI_MODEL, _env_float

# ── ① 検索（load_f3）─────────────────────────────────────────────
# 検索件数。int env は core.settings の慣行どおり fail-fast
# （不正値は import 時 ValueError＝設定ミスに即気づける）。
INQUIRY_TOP_K = int(os.environ.get("INQUIRY_TOP_K", "10"))

# 棄却時に related として返す近傍ナレッジの最大件数（Evidence 換算・D-9 単位）
INQUIRY_RELATED_LIMIT = int(os.environ.get("INQUIRY_RELATED_LIMIT", "3"))

# ── ②③ LLM（十分性判定・引用付き回答生成）───────────────────────
# デフォルトは事前レビューと同一モデル（GEMINI_MODEL）。
# `or` で空文字セット時（CI/コンテナで起きがち）もフォールバックさせる。
INQUIRY_MODEL = os.environ.get("INQUIRY_MODEL") or GEMINI_MODEL

# ── (b) 起票管理（store.py）──────────────────────────────────────
# Firestore コレクション名。E2E検証等で本体データと分けたい場合に env で切替可能
INQUIRY_FIRESTORE_COLLECTION = (
    os.environ.get("INQUIRY_FIRESTORE_COLLECTION") or "inquiries"
)

# ── ④ 接地検査ゲート ─────────────────────────────────────────────
# Check Grounding API のスコアがこの値未満なら棄却に倒す（誤答より棄却）。
# 0.6 は仮値。フェーズ4の評価ハーネスで較正する（DESIGN §5・§7）。
# 閾値は較正で頻繁に差し替えるため、不正値で起動不能にしない安全パーサ経由。
INQUIRY_GROUNDING_THRESHOLD = _env_float("INQUIRY_GROUNDING_THRESHOLD", 0.6)
