# NuRO 事前レビュー Phase 3 実装まとめ

最終更新：2026-05-21

---

## Phase 3 の目的

Phase 2 までの `load_supplement()` は補足資料 Excel のテキスト部分しか読めていなかった。
Phase 3 では写真・図面（画像）を Gemini でキャプション化して Vertex AI Search に投入し、
画像の内容もレビューの根拠として使えるようにする。

また本 Phase では、レビューエージェントの実行基盤を **Google ADK 2.0 に移行**した（インフラ改善）。

| Phase 2 の限界 | Phase 3 での解決策 |
|---|---|
| 補足資料の写真・図面情報が使えない | Gemini でキャプション生成 → Vertex AI Search で検索 |
| Tool1〜4 の並列実行が `asyncio.gather()` 手書き | ADK 2.0 の `Workflow` + `FunctionNode` で宣言的に並列化 |
| Phase 3 以降のノード追加に改修コストが必要 | `parallel_nodes` タプルへの1行追加のみで拡張可能 |

---

## ファイル構成（Phase 3 で追加・変更したもの）

```
nuro-ai-platform/
├── apps/
│   └── backend/app/
│       ├── agents/reviewer/
│       │   ├── knowledge_loader.py          ← load_supplement() を Vertex AI Search 化
│       │   ├── reviewer_agent.py            ← ADK run_workflow() 呼び出しに変更
│       │   ├── _review_logic.py             ← 新規追加：循環import対策の中立モジュール
│       │   └── adk/                         ← 新規追加：ADK 2.0 Workflow 実装
│       │       ├── __init__.py
│       │       ├── state_keys.py            ← Session State キー定数
│       │       ├── runner.py                ← Workflow 組み立て + run_workflow()
│       │       └── agents.py                ← FunctionNode 関数定義（Tool1〜5 + synthesis）
│       └── core/
│           └── settings.py                  ← VERTEX_SEARCH_SUPPLEMENT_* を追加
├── scripts/
│   ├── create_datastores.py                 ← nuro-supplement-knowledge を追加
│   ├── ingest_knowledge.py                  ← --target supplement を追加
│   └── generate_supplement_captions.py      ← 新規追加：キャプション生成スクリプト
├── docs/
│   └── ADK/
│       └── ADK_DESIGN.md                    ← 新規追加：ADK 導入設計書
├── apps/backend/tests/
│   └── test_review_e2e.py                   ← supplement テスト2件追加（計35件）
└── pyproject.toml                           ← python-pptx を依存追加
```

---

## ADK 2.0 移行（インフラ改善）

### 移行の背景

Phase 2 完了後、将来の Agentic RAG 移行コストをゼロにする目的で
`reviewer_agent.py` の実行基盤を ADK 2.0 に移行した。
詳細は `docs/ADK/ADK_DESIGN.md` を参照。

### Workflow 構造

```
START ──fan-out──┬── f2_node         ──┐
                 ├── f3_own_node     ──┤
                 ├── f3_all_node     ──┼── join ── rule_check_node ── synthesis_node
                 ├── similar_node    ──┤
                 └── supplement_node ──┘
```

### 主な変更内容

| 変更 | 内容 |
|---|---|
| `reviewer_agent.run_review()` | `run_workflow()` を呼ぶだけに簡略化（外部 I/F は Phase 1 から不変） |
| `_review_logic.py` 新規作成 | 循環 import を防ぐ中立モジュール。`detect_plan_diff` / `_build_prompt` 等を収容 |
| `adk/` ディレクトリ新規作成 | `runner.py`（Workflow 組み立て）+ `agents.py`（FunctionNode 定義） |
| `review.py` バグ修正 | 棄却時のレスポンスが常に `"saved"` だった問題を `"discarded"` に修正 |

### ADK 2.0 で発覚した破壊的変更

`run_async()` の `state_delta` は FunctionNode 実行後イベントにのみ適用される仕様に変更された。
初期値の注入には `create_session(state=initial_state)` を使うこと（詳細: `ADK_DESIGN.md` Section 4-4）。

---

## マルチモーダル補足資料 RAG（Tool 4 拡張）

### 処理フロー

```
【前処理：一度だけ実行】

data/knowledge/supplement/
  ├── *.xlsx  ─── openpyxl で画像バイト列 + 周辺セルテキスト抽出
  └── *.pptx  ─── python-pptx で画像バイト列 + スライドテキスト抽出
                          │
                          ▼
              Gemini 2.5 Flash（マルチモーダル）
              「{context_text} と書かれた枠の写真です。工事状態を説明してください」
                          │
                          ▼ キャプション生成
              data/knowledge/supplement_captions/*.json  ← 確認・修正用の中間ファイル
                          │
                          ▼ 投入
              Vertex AI Search（nuro-supplement-knowledge）

【レビュー実行時：毎回】

knowledge_loader.load_supplement()
  → 費目クエリで Vertex AI Search のキャプションをハイブリッド検索
  → 上位 N 件を synthesis_node のプロンプトに組み込む
```

検索対象はキャプション（テキスト）。画像は前処理（キャプション生成）にのみ使い、
レビュー実行時には使わない。

### インフラ構成（Phase 3 で追加）

| データストアID | 対象 | エンジンID |
|---|---|---|
| `nuro-supplement-knowledge` | 補足資料キャプション | `nuro-supplement-engine` |

`.env` に追加が必要な値：

```
VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID=nuro-supplement-knowledge
VERTEX_SEARCH_SUPPLEMENT_ENGINE_ID=nuro-supplement-engine
```

### generate_supplement_captions.py（新規）

```bash
uv run python scripts/preliminary_review/generate_supplement_captions.py           # 全ファイル処理
uv run python scripts/preliminary_review/generate_supplement_captions.py --file 東北電力_補足.xlsx
uv run python scripts/preliminary_review/generate_supplement_captions.py --dry-run # 画像抽出のみ確認
```

出力 JSON（`data/knowledge/supplement_captions/*.json`）の1件の構造：

| フィールド | 内容 |
|---|---|
| `id` | `{utility_name}_{source_file}_{image_index:03d}` |
| `caption` | Gemini が生成したキャプション（検索対象テキスト） |
| `utility_name` | 電力会社名（ファイル名の先頭から推定） |
| `source_file` | 元ファイル名 |
| `construction_name` | 工事名（Excel A1 セル or PPTX スライド1テキスト） |
| `context_text` | 画像周辺のテキスト（「撤去後」等） |
| `original_format` | `"excel"` or `"pptx"` |

### ingest_knowledge.py（追記）

```bash
uv run python scripts/preliminary_review/ingest_knowledge.py --target supplement
```

Vertex AI Search への投入ドキュメント構造：

| フィールド | 内容 | 役割 |
|---|---|---|
| `id` | `{utility_name}_{source_file}_{index:03d}` | ドキュメント識別子 |
| `content.raw_bytes` | キャプションテキスト | BM25+ベクトル検索の対象 |
| `struct_data.knowledge_type` | `"SUPPLEMENT"` | 種別識別 |
| `struct_data.utility_name` | 電力会社名 | メタデータ（検索フィルタには使わない） |
| `struct_data.construction_name` | 工事名 | 検索結果の表示用 |
| `struct_data.context_text` | 「撤去後」等 | 検索結果の表示用 |
| `struct_data.original_format` | `"excel"` or `"pptx"` | 検索結果の表示用 |
| `struct_data.caption` | キャプション（再掲） | 検索結果で返す用 |

### knowledge_loader.py — load_supplement() の変更

> **I/F（引数・戻り値）は変更なし。内部実装のみ差し替え。**

```python
def load_supplement(caller_role, utility_name=None, fee_type=None, limit=20) -> list[dict]
    # Phase 2: data/knowledge/supplement/ の Excel からテキスト読み込み（廃止）
    # Phase 3: Vertex AI Search でキャプションをハイブリッド検索
    # VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID 未設定時は空リストにフォールバック
    # NuROは全電力会社の補足資料を参照可能（utility_name でフィルタしない）
```

`reviewer_agent.py`・`adk/agents.py`（`supplement_node()`）・API エンドポイント・フロントエンドへの変更なし。

---

## データ投入の実行手順（GCP接続が必要）

```bash
# 1. 補足資料ファイルを配置
#    data/knowledge/supplement/ に .xlsx / .pptx を配置

# 2. データストアを作成（初回のみ）
uv run python scripts/preliminary_review/create_datastores.py

# 3. キャプション生成（中間JSONで内容確認してから投入）
uv run python scripts/preliminary_review/generate_supplement_captions.py

# 4. Vertex AI Search に投入
uv run python scripts/preliminary_review/ingest_knowledge.py --target supplement

# 5. .env に環境変数を追加
# VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID=nuro-supplement-knowledge
# VERTEX_SEARCH_SUPPLEMENT_ENGINE_ID=nuro-supplement-engine
```

---

## テスト

35/35 PASSED（Phase 3 で2件追加）

| テストケース | 内容 |
|---|---|
| `test_supplement_returns_empty_for_denryoku` | 電力ロールは補足資料を参照できない |
| `test_supplement_returns_empty_when_datastore_not_configured` | データストアID未設定時は空リストにフォールバック |

---

## Phase 3 の既知の制約

| # | 制約 | 対応状況 |
|---|---|---|
| ① | 対応ファイル形式は Excel・PPTX のみ（PDF・Word は未対応） | 補足資料フォーマット確定後に判断 |
| ② | `load_similar_work()` はデータ未入手のためスタブ（空リスト） | データ入手後に有効化 |
| ③ | reactor_type フィルタは struct_data 拡張後に有効化 | 未対応のまま継続 |
| ④ | キャプション精度が不十分な場合の第2選択肢（Vertex AI Multimodal Embeddings）は未実装 | NuRO評価後に判断 |

---

## 用語集（Phase 3 追加分）

| 用語 | 説明 |
|---|---|
| キャプション | Gemini が画像 + 周辺テキストから生成した工事状態の説明テキスト |
| ADK 2.0 | Google Agent Development Kit v2.0。Workflow/FunctionNode/JoinNode でノード実行グラフを定義する |
| FunctionNode | Python 関数を ADK Workflow のノードとしてラップするクラス。LLMなし・決定論的 |
| JoinNode | 複数の並列ノードが全て完了するまで待機するノード |
| InMemorySessionService | ADK のノード間 state 共有を管理するセッションストア（プロセス内メモリ） |
| run_in_executor | 同期関数をスレッドプールで非同期実行して真の並列 I/O を実現する asyncio の機能 |
