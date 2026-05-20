# NuRO 事前レビュー Phase 2 実装まとめ

最終更新：2026-05-20

-----

## Phase 2 の目的

Phase 1（構造化フィルタ型RAG）で確認した基本動作を前提に、以下の課題を解消する。

| Phase 1 の限界 | Phase 2 での解決策 |
|---|---|
| 同義語・表記ゆれに対応できない（「費用低減」と「コスト削減」が別扱い） | Vertex AI Search のハイブリッド検索（BM25+ベクトル）で解決 |
| reactor_type（炉型）の絞り込みが機能しない | Vertex AI Search のフィルタ（struct_data）で対応（後半対応） |
| ナレッジ増加時の取りこぼしリスク | Rerankingで上位N件に絞ることで解決 |
| 検索品質の可視化ができない | Langfuseによるトレーシングで各Toolの取得状況を記録 |

-----

## ファイル構成（Phase 2 で追加・変更したもの）

```
nuro-ai-platform/
├── apps/
│   └── backend/app/
│       ├── agents/reviewer/
│       │   ├── knowledge_loader.py   ← 内部実装をVertex AI Searchに差し替え
│       │   ├── reviewer_agent.py     ← retrieval_trace追加・Langfuse @observe追加
│       │   ├── _excel_reader.py      ← 新規追加：データ投入用Excel読み込み専用モジュール
│       │   └── criteria_loader.py    ← 新規追加：レビュー観点YAMLローダー
│       ├── api/
│       │   ├── main.py               ← CORSにPATCHメソッドを追加
│       │   ├── models.py             ← FeedbackSyncRequest追加・FeedbackRequestにsession_id追加
│       │   └── routes/
│       │       ├── review.py         ← feedbacksに棄却も保存・syncエンドポイント追加・直接パス取得
│       │       ├── sessions.py       ← 新規追加：セッション一覧(review_status付き)・完了マーク
│       │       └── upload.py         ← GCS連携追加（gcs_client使用）
│       └── core/
│           ├── settings.py           ← Vertex AI SearchのID定数を追加
│           ├── ai_client.py          ← call_geminiにsystem_instruction追加・@observe追加
│           ├── gcs_client.py         ← 新規追加：GCSアップロード・署名付きURL生成
│           └── langfuse_client.py    ← 新規追加：RAGトレーシング
├── data/
│   └── review_criteria/
│       └── frameB_MRC1.yaml          ← 新規追加：レビュー観点定義（フレーム・シート別）
├── scripts/
│   ├── create_datastores.py          ← 新規追加：データストア作成（一度だけ実行）
│   └── ingest_knowledge.py           ← 新規追加：ナレッジ投入スクリプト
├── Makefile                          ← 新規追加：開発用起動コマンド
├── docker-compose.langfuse.yml       ← 新規追加：Langfuseローカル環境
└── .env                              ← Vertex AI Search ID・エンジンIDを追記
```

-----

## インフラ構成（Phase 2 で追加）

### Vertex AI Search データストア

| データストアID | 対象ナレッジ | エンジンID |
|---|---|---|
| `nuro-f2-knowledge` | F2ナレッジ（NuRO内有の知見） | `nuro-f2-search` |
| `nuro-f3-knowledge` | F3ナレッジ（電力別問合せ履歴） | `nuro-f3-search` |

`.env` に設定済みの値：

```
GOOGLE_CLOUD_PROJECT=adk-tutorial-492303
GOOGLE_CLOUD_LOCATION=global
VERTEX_SEARCH_F2_DATASTORE_ID=nuro-f2-knowledge
VERTEX_SEARCH_F3_DATASTORE_ID=nuro-f3-knowledge
VERTEX_SEARCH_F2_ENGINE_ID=nuro-f2-search
VERTEX_SEARCH_F3_ENGINE_ID=nuro-f3-search
```

### Langfuse（RAGトレーシング）

ローカル環境で Docker Compose により起動する。

```bash
docker compose -f docker-compose.langfuse.yml up -d
# http://localhost:3000 でUI確認
# Settings → API Keys でキーを発行して .env に記入
```

`.env` に以下を追記すると有効化される（未設定の場合はノーオペレーションで動作に影響なし）：

```
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=http://localhost:3000
```

-----

## knowledge_loader.py — Phase 2 変更内容

> **設計方針変わらず**：I/F（引数・戻り値）は全Phase通じて変更しない。
> 内部実装（検索バックエンド）のみ Vertex AI Search に差し替え。

### 主要な内部関数

```python
def _get_search_client() -> discoveryengine.SearchServiceClient
    # Vertex AI Search クライアント（遅延初期化・シングルトン）

def _serving_config(datastore_id: str) -> str
    # エンジンID設定済みならエンジン経由、なければデータストア直接

def _build_filter(conditions: dict[str, str]) -> str
    # {'utility_name': 'AA電力'} → 'utility_name: ANY("AA電力")' に変換

def _search(datastore_id, query, filter_str, limit) -> list[dict]
    # BM25+ベクトルのハイブリッド検索を実行
    # query が空の場合は "工事" で代替（Vertex AI Search は空クエリ不可）

def _to_record(result: SearchResult) -> dict
    # struct_data と content.raw_bytes をフラットな辞書に変換
```

### 公開インターフェース（全Phase共通・変更なし）

```python
def load_f2(caller_role, fee_type=None, limit=30) -> list[dict]
    # caller_role=="電力" は空リスト（F2はNuROのみ参照可）
    # filter: caller_role_required=NuRO + クエリ: fee_type

def load_f3(caller_role, utility_name=None, reactor_type=None,
            fee_type=None, sheet_name=None, limit=50) -> list[dict]
    # caller_role=="電力" かつ utility_name 未指定は空リスト
    # NuRO は全社参照可（utility_name 指定で絞り込み）
    # reactor_type フィルタ: Phase 2後半で struct_data 拡張後に有効化予定（TODOコメント済み）

def load_similar_work(caller_role, reactor_type=None,
                      fee_type=None, limit=20) -> list[dict]
    # Phase 2 現在：データ未入手のためスタブ（空リスト）
    # データ入手後 VERTEX_SEARCH_SIMILAR_WORK_DATASTORE_ID を追加して有効化

def load_supplement(caller_role, utility_name=None,
                    fee_type=None, limit=20) -> list[dict]
    # Phase 2 現在：data/knowledge/supplement/ の Excel からテキスト読み込み
    # Phase 3：Gemini 3 マルチモーダルで写真・図面も処理予定
```

-----

## _excel_reader.py — 新規追加

Phase 2 で「検索バックエンド（knowledge_loader.py）」と「データ投入用読み込み（_excel_reader.py）」を明確に分離した。

```python
def read_all_f2() -> list[dict]
    # F2スキーマの YAML を自動検出し、対応する Excel を全件読み込む

def read_all_f3() -> list[dict]
    # F3スキーマの YAML を自動検出し、対応する Excel を全件読み込む
```

**Phase 1 からの処理ロジック継承：**

| 処理 | 内容 |
|---|---|
| スキーマ自動検出 | `data/knowledge/schema/f2_*_schema.yaml` / `f3_*_schema.yaml` を全て読み込む |
| ffill（前方補完） | セル結合による空欄を上の行の値で埋める |
| 縦持ち変換 | NuRO確認↔電力回答の往復を1行ずつに展開（message_id, message_direction, message_content） |

-----

## データ投入スクリプト

### create_datastores.py（一度だけ実行）

```bash
uv run python scripts/create_datastores.py
```

- F2・F3の2つのデータストアを Vertex AI Search に作成する
- 既存の場合はスキップ（冪等性あり）
- 完了後、出力されたデータストアIDを `.env` に追記する

### ingest_knowledge.py（ナレッジ更新時に再実行）

```bash
uv run python scripts/ingest_knowledge.py           # F2・F3両方
uv run python scripts/ingest_knowledge.py --target f2
uv run python scripts/ingest_knowledge.py --target f3
```

**ドキュメント構造（Vertex AI Search への投入形式）：**

| フィールド | 内容 | 役割 |
|---|---|---|
| `id` | `f2_{message_id}` 形式 | ドキュメント識別子 |
| `content.raw_bytes` | メッセージ本文テキスト | BM25+ベクトル検索の対象 |
| `struct_data.knowledge_type` | `"F2"` or `"F3"` | 種別識別 |
| `struct_data.utility_name` | 電力会社名 | フィルタリング用 |
| `struct_data.fee_type` | 費目 | フィルタリング用 |
| `struct_data.caller_role_required` | `"NuRO"` or `"any"` | 権限制御用 |
| `struct_data.message_content` | メッセージ本文（再掲） | 検索結果で返す用 |

バッチサイズ100件でインポート。再実行時は INCREMENTAL モードで上書き（最新化）。

-----

## reviewer_agent.py — Phase 2 変更内容

### retrieval_trace の追加

各Toolの検索結果を `retrieval_trace` として記録し、レスポンスに含めるようにした。

```python
retrieval_trace.append({
    "tool":    "Tool1（F2ナレッジ）",
    "query":   fee_type or "（クエリなし）",
    "count":   len(f2_knowledge),
    "top_ids": [r.get("_doc_id", "") for r in f2_knowledge[:3]],
})
```

フロントエンドのRAG詳細パネルやLangfuseで各Toolの取得状況を可視化できる。

### Langfuse トレーシング（@observe デコレーター）

```python
from langfuse import observe

@observe(name="review", capture_input=True, capture_output=True)
async def run_review(...):
```

`LANGFUSE_PUBLIC_KEY` と `LANGFUSE_SECRET_KEY` が `.env` に設定されている場合のみ有効。
未設定の場合は動作に影響なし。

### Tool 2 の分割（2a/2b）

Phase 1 では F3 を1回のみ検索していたが、Phase 2 では自社・他社を別クエリで取得するよう分割した。

| Tool | 内容 | Phase 1 | Phase 2 |
|---|---|---|---|
| Tool 1 | F2ナレッジ | 構造化フィルタ | Vertex AI Search |
| Tool 2a | F3ナレッジ（自社） | Tool 3 として一括 | 自社のみで個別検索 |
| Tool 2b | F3ナレッジ（他社類似） | Tool 3 として一括 | 全社で個別検索 |
| Tool 3 | 類似工事データ | スコープ外 | スタブ（データ未入手） |
| Tool 4 | 補足資料 | スタブ | Excelテキスト読み込み |
| Tool 5 | 計画・実績差分 | ルールベース | 変更なし |

-----

## APIエンドポイント（Phase 2 変更・追加）

### GET /api/sessions — セッション一覧（新規追加）

`review_status`・進捗情報付きで全セッションを返す（NuROレビュー画面のサイドバー用）。

```json
[
  {
    "session_id": "...",
    "utility_name": "AA電力",
    "session_name": "AA工事",
    "review_status": "completed",
    "progress": {"total": 10, "decided": 10}
  }
]
```

### PATCH /api/sessions/{session_id}/complete — レビュー完了マーク（新規追加）

保存ボタン押下時に呼ぶ。`sessions` ドキュメントに `review_completed: true` を書き込む。
`review_status` が `"completed"` に変わり、サイドバーの「レビュー済み」タブへ移動する。

### POST /api/review/{review_id}/feedbacks/sync — フィードバック一括同期（新規追加）

保存ボタン押下時に現在のフィードバック全件を feedbacks 配列で**完全上書き**する。
リアルタイム保存の漏れを補完し、ページリロード後も正確に復元できることを保証する。

```json
{
  "feedbacks": [
    {"item_id": "review_001", "decision": "accept"},
    {"item_id": "review_002", "decision": "reject"}
  ],
  "session_id": "..."
}
```

`session_id` を受け取ることで `collection_group` クエリ（Firestoreインデックス必要）を回避し、
直接パス `sessions/{session_id}/review_results/{review_id}` でドキュメントを取得する。
`submit_feedback`・`undo_feedback` も同様に `session_id` を使った直接パス取得に統一済み。

### GET /api/review/{session_id}/result — レビュー結果復元（変更）

レスポンスに `feedbacks` フィールドを追加。ページリロード時に承諾/棄却状態を復元する。

```python
return {
    "review_id": ...,
    "review_items": [...],
    "feedbacks": [...],   # 追加：各エントリに decision: "accept"|"reject"
    ...
}
```

### POST /api/review/{review_id}/feedback — フィードバック保存（変更）

承諾・棄却どちらも `feedbacks` 配列に保存するよう変更（旧: 棄却は保存しなかった）。
`session_id` をリクエストボディで受け取り直接パス取得。

### DELETE /api/review/{review_id}/feedback/{item_id} — フィードバック取り消し（変更）

`session_id` をクエリパラメータで受け取り直接パス取得。
承諾/棄却どちらのundoでも `decided_count` をデクリメントするよう修正。

### GET /api/review/stats — Phase 2移行判断指標（新規追加）

```json
{
  "total_accepted": 15,
  "total_rejected": 8,
  "rejection_rate": 0.347,
  "monthly_total": 23,
  "phase2_trigger": false,
  "phase2_reasons": [],
  "daily": [
    {"date": "2026-05-19", "accepted": 3, "rejected": 1}
  ]
}
```

| 指標 | Phase 2 移行推奨トリガー |
|---|---|
| rejection_rate | 50%超えで `phase2_trigger: true` |
| monthly_total | 10件超で `phase2_reasons` に追記 |

承諾・棄却のたびに `review_stats/{YYYY-MM-DD}` ドキュメントへ日次集計する。

### POST /api/review — retrieval_trace 追加

レスポンスの `ReviewResponse` に `retrieval_trace` フィールドを追加した。

```python
class ReviewResponse(BaseModel):
    review_id: str
    review_items: list[ReviewItem]
    summary: str
    reviewed_at: str
    mappings: list[dict] = []
    retrieval_trace: list[dict] = []  # Phase 2 追加
```

-----

## Firestoreデータ構造（Phase 2 追加）

```
sessions/{session_id}/
  session_id, utility_name, frame_name, sheet_name
  mappings: list[dict]
  created_at
  reviewed: bool
  review_completed: bool       ← 手動「保存」ボタンで True にセット

  review_results/{review_id}/
    review_items, summary, reviewed_at
    total_count: int           ← 指摘件数
    decided_count: int         ← 承諾/棄却済み件数（進捗管理用）
    feedbacks: list[dict]      ← 承諾・棄却どちらも保存（decision: "accept"|"reject"）
    ※ retrieval_trace は Firestore に保存しない（デバッグ用途のみ）

review_stats/{YYYY-MM-DD}/         ← Phase 2 追加
  date, accepted, rejected
```

**review_status（DBに保存しない・APIが都度算出）**

| 値 | 条件 |
|---|---|
| `"not_reviewed"` | review_results サブコレクションが存在しない |
| `"in_progress"` | review_results あり・decided_count < total_count かつ review_completed=False |
| `"completed"` | decided_count >= total_count、または review_completed=True |

-----

## langfuse_client.py — 新規追加

エンジニア向けのRAGトレーシング基盤。`.env` に設定なしの場合は全操作がノーオペレーションになり、アプリの動作に影響しない。

```python
def is_langfuse_enabled() -> bool
def get_langfuse() -> Langfuse | None
    # 遅延初期化・シングルトン
```

**記録される内容：**

- `reviewer_agent.run_review()` の入力（session_id, utility_name, fee_type）と出力（指摘件数）
- 承諾/棄却フィードバックのスコア（accept=1.0 / reject=0.0）
- トレース単位は `review_id`（`lf.score(trace_id=review_id, ...)`）

-----

## Phase 2 の既知の制約（要件通り・コードにTODO済み）

| # | 制約 | 対応状況 |
|---|---|---|
| ① | load_similar_work() はデータ未入手のためスタブ（空リスト） | データ入手後に有効化 |
| ② | reactor_type フィルタは struct_data 拡張後に有効化（TODOコメント済み） | Phase 2 後半対応 |
| ③ | 補足資料の写真・図面は未対応 | Phase 3 で Gemini 3 マルチモーダルで対応 |

-----

## Phase 3 への移行について

Phase 3 は `load_supplement()` の内部実装のみを変更する。

```
【Phase 3 の処理】
補足資料Excel・PPTX
  ↓ openpyxl で画像を抽出
  ↓ 画像近傍のテキスト情報を取得（「撤去後」等のセル名）
  ↓ Gemini 3 に渡す（「撤去後と書かれた枠の写真です。工事状態を説明してください」）
  ↓ キャプション生成（「PPパネルが完全に撤去されており工事完了状態」）
  ↓ キャプションを Vertex AI Search に投入（ベクトル化して保存）

【レビュー実行時】
Vertex AI Search でキャプションを検索
→ 関連する補足資料の情報を取得して Gemini に渡す
```

`knowledge_loader.py` の `load_supplement()` 内部のみ変更する。
`reviewer_agent.py`・APIエンドポイント・フロントエンドへの影響なし。

-----

## criteria_loader.py — 新規追加

レビュー観点をコードから分離し、YAMLで管理するローダー。

```python
def load_criteria(frame_name: str, sheet_name: str) -> list[dict]
    # data/review_criteria/{frame}_{sheet}.yaml を読み込む
    # status=active の観点のみ返す（draft は含めない）
    # ファイルが存在しない場合は空リスト
```

**YAMLファイルの場所:** `data/review_criteria/frameB_MRC1.yaml`

設計方針: レビュー観点の追加・変更はYAMLのみで完結する。`criteria_loader.py` のI/Fは変えない。
YAMLで定義した観点は `call_gemini()` の `system_instruction` としてGeminiに注入される。

-----

## gcs_client.py — 新規追加

Google Cloud Storage の操作を担うクライアントモジュール。

```python
def upload_file(local_path, gcs_path, content_type) -> str
    # ローカルファイルをGCSにアップロードしてGCS URIを返す

def upload_bytes(data: bytes, gcs_path, content_type) -> str
    # バイト列をGCSにアップロード

def generate_signed_url(gcs_path, expiration_minutes=60) -> str
    # 署名付きURL（一時ダウンロードリンク）を生成

def sanitize_path_component(name: str) -> str
    # パストラバーサル対策：英数字・ハイフン・アンダースコア以外を除去
```

`upload.py` がGCSを使うよう変更済み。転記済みExcelファイルはGCSに保存され、
フロントエンドからは署名付きURLでダウンロードできる。

-----

## ai_client.py — Phase 2 変更内容

### system_instruction 引数の追加

```python
def call_gemini(prompt, model_name="gemini-2.5-flash", system_instruction: str = "") -> str
```

`criteria_loader.py` が生成したレビュー観点テキストを `system_instruction` として渡せるようになった。
`temperature=0.0` を固定し、同一入力で同一出力を保証する。

### Langfuse @observe デコレーター追加

```python
@observe(name="gemini_call", as_type="generation", capture_input=True, capture_output=True)
def call_gemini(...):
```

`LANGFUSE_PUBLIC_KEY` が設定されている場合のみ有効。Gemini呼び出しごとにトレースが記録される。

-----

## Makefile — 新規追加

開発用の起動コマンドをまとめたファイル。

```bash
make backend   # バックエンドのみ起動（apps/backend/ のみ監視）
make frontend  # フロントエンドのみ起動
make dev       # バックエンド・フロントエンドを同時起動
```

-----

## 用語集

| 用語 | 説明 |
|---|---|
| Vertex AI Search | Google の RAG 向け検索基盤。BM25+ベクトル検索を自動ブレンドするハイブリッド検索 |
| discoveryengine_v1 | Vertex AI Search の Python SDK（旧 Discovery Engine） |
| serving_config | 検索リクエストの宛先パス。エンジン経由とデータストア直接の2種類がある |
| struct_data | Vertex AI Search のドキュメントに付与するメタデータ（フィルタリング・権限制御用） |
| Reranking | 検索結果の上位 N 件を再スコアリングして精度を上げる処理（Phase 2 後半で設定予定） |
| retrieval_trace | 各 Tool の検索クエリ・取得件数・代表ドキュメント ID を記録したログ |
| Langfuse | LLM アプリのトレーシング・評価ツール。プロンプト・スコア・フィードバックを可視化 |
| @observe | Langfuse の Python デコレーター。関数の入出力を自動的にトレース |
| INCREMENTAL | Vertex AI Search のインポートモード。既存ドキュメントを上書きして最新化する |
| caller_role_required | struct_data に付与した権限フラグ。"NuRO" の場合 F2 ナレッジのみ参照可 |
