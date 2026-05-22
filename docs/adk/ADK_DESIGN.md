# Google ADK 2.0 導入設計書

作成日：2026-05-21  
対象：NuRO AI Platform — レビューエージェント（Agentic RAG）

---

## 1. ADK を採用した理由

### 1-1. 背景

Phase 1（構造化フィルタ型RAG）完了後、Phase 2 の Vertex AI Search 対応に合わせ、
レビューエージェント（`reviewer_agent.py`）の実行基盤を Google ADK 2.0 に移行した。

以下の課題意識が採用の動機である。

| 課題 | 手動実装（移行前） | ADK 2.0（移行後） |
|---|---|---|
| Tool1〜4 の並列実行 | `asyncio.gather()` を手書き | `Workflow` のエッジ定義で宣言的に並列化 |
| Tool 追加時の変更コスト | `run_review()` 本体に追記が必要 | `parallel_nodes` タプルに1行追加するだけ |
| Phase 3 マルチモーダル拡張 | `run_review()` の大幅改修が必要 | `MultimodalNode` を並列グループに追加するだけ |
| 実行フローの可視化 | コードを読まないとフローが分からない | Workflow グラフが宣言的に表現されている |
| スケールアウト準備 | PoC 外 | ADK の分散実行基盤に差し替え可能 |

### 1-2. 将来コストの最小化

本番目標（2027年）でも ADK を使う方針のため、PoC 段階から ADK に乗ることで
**移行コストをゼロにする**判断を行った。

ADK 1.x → 2.0 の変更は breaking change を含む（後述）。PoC が ADK 2.0 で稼働していれば
本番移行時に再移行の工数は発生しない。

### 1-2-1. 現状における ADK の正直な評価

現在の実装（固定順で全 Tool を実行する固定パイプライン）は、**`asyncio.gather()` で代替できる**。
ADK の `Workflow`/`FunctionNode`/`InMemorySessionService`/`Runner` という複雑な構成は、
固定パイプラインに対してはオーバーエンジニアリングである。

ADK が本領を発揮するのは以下の条件が満たされたときである。

| 条件 | ADK の恩恵 |
|---|---|
| LLM が「どの Tool を呼ぶか」を動的に判断する | `LlmAgent` + Tool 登録で自然に実現 |
| 「結果が薄い場合に再検索」のループが必要 | ReAct Loop ノードが使える |
| Tool4 をマルチモーダル Sub Agent に昇格させる | `LlmAgent` をノードとして組み込めば自律動作する |
| 本番で水平スケールアウトする | `FirestoreSessionService` に1行で差し替え可能 |

これらはいずれも要件書 Section 4-5 の「本格的な Agent 化トリガー」が発動してから必要になる。
PoC での ADK 採用は「そのトリガーが発動したときに移行コストをゼロにする」先行投資として位置づける。

### 1-2-2. ADK なしで Agentic RAG に移行すると何が起きるか

ADK を使わずに「LLM が Tool を選ぶ」Agentic RAG を実装しようとすると、
以下の全てを `reviewer_agent.py` に手書きすることになる。

**① ReAct ループの自前実装**

```python
# ADK なしの場合
async def run_review_agentic(...):
    messages = [{"role": "user", "content": initial_prompt}]
    while True:
        response = await call_gemini_with_tools(messages, tool_definitions)
        if response.finish_reason == "STOP":
            break
        for tool_call in response.tool_calls:
            result = await dispatch_tool(tool_call.name, tool_call.args)
            messages.append({"role": "tool", "content": result})
    return parse_final_response(response)
```

ループ終了条件・最大試行回数・並列 Tool 呼び出しの制御・エラーリカバリが全て `reviewer_agent.py` に集まる。

**② Phase 3 Sub Agent 追加時に爆発する**

マルチモーダル Sub Agent は「自分で画像を解釈して返す」独自の ReAct ループを持つ。
親子間の状態受け渡し・エラー伝播・並列実行を手書きすると `reviewer_agent.py` が事実上のフレームワークになる。

**③ スケールアウト時の状態共有**

自前の `dict` による状態管理は複数 uvicorn ワーカー間で共有できない。
ADK なら `InMemorySessionService` → `FirestoreSessionService` の1行差し替えで解決する。

### 1-3. ADK バージョン選定

| バージョン | リリース日 | 採用可否 |
|---|---|---|
| v1.34.0 | 2026-05-18 | 不採用（v1 最終版・v2 移行前） |
| v2.0.0b1 | 2026-04-22 | Beta — Workflow/FunctionNode/JoinNode が導入 |
| **v2.0.0** | **2026-05-19** | **採用（GA リリース）** |

`pyproject.toml` の依存バージョン指定：

```toml
"google-adk>=2.0.0"
```

---

## 2. ADK 2.0 の主な変更点（v1.x との差分）

### 2-1. 非互換 API 変更

| v1.x | v2.0 | 備考 |
|---|---|---|
| `SequentialAgent` | `Workflow` + 直列エッジ | 削除 |
| `ParallelAgent` | `Workflow` + fan-out エッジ | 削除 |
| `AgentExecutor` | `Runner` | 統合 |
| `state_delta`（実行前注入） | `create_session(state=...)` で初期化 | `state_delta` は FunctionNode 実行後にのみ適用される仕様に変更 |

### 2-2. v2.0 で追加された主要 API

| API | 役割 |
|---|---|
| `Workflow` | ノードとエッジで実行グラフを宣言する |
| `FunctionNode` | Python 関数を Workflow の1ノードとしてラップする |
| `JoinNode` | 複数の並列ノードが全て完了するまで待機する |
| `START` | Workflow の始点を示す定数 |
| `Runner` | Workflow を実行する（`run_async()` でイベントストリームを返す） |
| `InMemorySessionService` | ノード間の state 共有を管理するセッションストア |
| `Context` | 各 FunctionNode が受け取る実行コンテキスト（`ctx.state` で読み書き） |

---

## 3. 本 PoC における Workflow アーキテクチャ

### 3-1. 処理フロー全体図

```
POST /api/review
      │
      ▼
reviewer_agent.run_review()  ← Langfuse @observe でトレース
      │
      ▼
adk/runner.run_workflow()
      │
      │  ┌─ create_session(state=initial_state) で入力値を注入
      │  └─ Runner.run_async() でイベントループ実行
      │
      ▼
┌─────────────────────────────────────────────────────┐
│                    Workflow                          │
│                                                      │
│  START ──fan-out──┬── f2_node         ──┐            │
│                   ├── f3_own_node     ──┤            │
│                   ├── f3_all_node     ──┼── join ──► │
│                   ├── similar_node    ──┤            │
│                   └── supplement_node ──┘            │
│                                         │            │
│                                   rule_check_node    │
│                                         │            │
│                                   synthesis_node     │
└─────────────────────────────────────────────────────┘
      │
      ▼
final_session.state → ReviewItem リスト + retrieval_trace
```

### 3-2. 各ノードの役割

| ノード | 対応Tool | 実装 | 実行方式 |
|---|---|---|---|
| `f2_node` | Tool1（F2ナレッジ） | `f2_knowledge_node()` | 並列（async + run_in_executor） |
| `f3_own_node` | Tool2a（F3自社） | `f3_own_knowledge_node()` | 並列（async + run_in_executor） |
| `f3_all_node` | Tool2b（F3他社） | `f3_all_knowledge_node()` | 並列（async + run_in_executor） |
| `similar_node` | Tool3（類似工事） | `similar_work_node()` | 並列（スタブ・データ未入手） |
| `supplement_node` | Tool4（補足資料） | `supplement_node()` | 並列（async + run_in_executor） |
| `join_node` | — | `JoinNode` | 並列グループの完了待ち |
| `rule_check_node` | Tool5（計画差分） | `rule_check_node()` | 直列（CPU のみ・sync def） |
| `synthesis_node` | Gemini レビュー生成 | `synthesis_node()` | 直列（async + run_in_executor） |

> **注意：FunctionNode は Sub Agent でも Tool でもない**
>
> ADK における「Tool」は LLM エージェントが「いつ・どれを・何回呼ぶか」を動的に判断して呼び出すものを指す。
> 「Sub Agent」は自前の LLM と ReAct ループを持つ自律的なエージェントを指す。
>
> 本実装の各 FunctionNode は LLM を持たず、自律的な判断も行わない。全ノードが**固定順で必ず実行される決定論的な処理ステップ**である。
> 「Tool」という名称は要件書上の便宜的な呼称であり、ADK の Tool とは別概念。

### 3-3. f3_own_node と f3_all_node を分けている理由

どちらも `knowledge_loader.load_f3()` を呼ぶが、`utility_name` の有無が異なる。

| ノード | `utility_name` | 意図 |
|---|---|---|
| `f3_own_node` | 申請電力会社名を渡す | 自社の過去事例を確実に取得（top-20 を自社分で埋める） |
| `f3_all_node` | `None`（全社検索） | 業界全体の類似事例を取得 |

分ける理由は2点：
1. **Gemini へのコンテキスト明示** — プロンプト内で `[F3own#N]`（自社）と `[F3all#N]`（他社）を別ラベルで渡すことで、「この会社が以前やったこと」と「業界の一般的な慣例」を区別した指摘を生成できる
2. **自社データの取得保証** — 全社検索の top-20 に自社レコードが1件も入らないケースへの対策

> **注意：** `f3_all` は全社検索のため `f3_own` の内容を含む（重複の可能性あり）。
> PoC ではこれを許容しているが、トークン効率を重視する場合は `f3_all` から自社分を除外する処理の追加を検討すること。
> また、自社/他社の区別が指摘品質に寄与しているかは、NuROのフィードバックデータが蓄積されてから評価する。

### 3-3. ファイル構成

```
apps/backend/app/agents/reviewer/
├── reviewer_agent.py      ← run_review() エントリーポイント（外部 I/F は Phase1 から不変）
├── _review_logic.py       ← 純粋なロジック関数群（循環 import を防ぐ中立モジュール）
├── knowledge_loader.py    ← Tool1〜4 の同期 I/F（全 Phase 通じて変更しない）
├── criteria_loader.py     ← Gemini system instruction 生成
└── adk/
    ├── __init__.py
    ├── state_keys.py      ← Session State キー定数（タイポ防止）
    ├── runner.py          ← Workflow 組み立て + run_workflow() 実行関数
    └── agents.py          ← FunctionNode 関数定義（Tool1〜5 + SynthesisNode）
```

---

## 4. 設計上の判断

### 4-1. `_review_logic.py` を分離した理由（循環 import 対策）

ADK 移行前は `reviewer_agent.py` に全ロジックが集約されていた。
`adk/agents.py` から `reviewer_agent.py` を import すると循環 import が発生するため、
共有ロジック（`detect_plan_diff`, `_build_prompt`, `_parse_review_response` 等）を
`_review_logic.py` に抽出して依存関係を一方向にした。

```
reviewer_agent.py  ──┐
                      ├── import ──► _review_logic.py
adk/agents.py      ──┘
```

`reviewer_agent.py` は後方互換のため `_review_logic.py` から re-export している。

### 4-2. `knowledge_loader.py` を run_in_executor でラップした理由

`knowledge_loader.py` の各関数（`load_f2`, `load_f3` 等）は Vertex AI Search の同期 I/F を
使用している。`FunctionNode` は `async def` をサポートするが、同期ブロッキング呼び出しを
`await` しただけでは並列化されない（イベントループをブロックする）。

`asyncio.get_running_loop().run_in_executor(None, sync_func, args)` で
**スレッドプールに投げることで真の並列 I/O** を実現している。

### 4-3. Session State のシリアライズ制約

ADK の Session State は JSON 直列化可能な型しか格納できない。
このため Pydantic モデル（`ReviewItem`）は `model_dump()` で dict に変換して保存し、
読み出し時に `ReviewItem(**d)` で復元するパターンを採用している。

`set` 型も格納できないため、`empty_cells`（本来 `set[str]`）は `list[str]` に変換して格納している。

### 4-4. 入力パラメータの注入方法

ADK 2.0 では `run_async()` の `state_delta` パラメータは **FunctionNode 実行後のイベントに
適用される**仕様になっており、実行前の初期値注入には使えない（PoC 中に発覚した破壊的変更）。

初期値は `create_session(state=initial_state)` で**セッション作成時に注入**する方式を採用した。

### 4-5. InMemorySessionService のシングルトン化

`_session_service = InMemorySessionService()` をモジュールレベルで宣言し、
アプリケーション起動中に1インスタンスを使い回している。

レビューは stateless なリクエスト単位の処理のため、毎回 `create_session()` で
**新規セッションを作成**して完了後は参照しなくなる（明示的な削除はしていない）。
長期運用時にメモリリークが懸念される場合は `delete_session()` の追加を検討すること。

### 4-6. Phase 3 拡張ポイント

`runner.py` の `_build_workflow()` 内の `parallel_nodes` タプルに
`MultimodalNode` を追加するだけで Phase 3 対応が完結するよう設計した。

```python
# runner.py — Phase 3 でここに1行追加するだけ
parallel_nodes = (f2_node, f3_own_node, f3_all_node, similar_node_fn, supp_node)
#                                                                  ↑ multimodal_node を追加
```

対応する FunctionNode 関数は `agents.py` に追加し、`runner.py` で import して登録する。
`reviewer_agent.py`・API エンドポイント・フロントエンドへの変更は不要。

---

## 5. Langfuse トレーシングとの連携

`run_review()` に付与した `@observe` デコレータは ADK 経由でも継続して動作する。
`call_gemini()` にも `@observe` が付いており、Gemini 呼び出しが個別のスパンとして記録される。

`run_in_executor` を使うと呼び出しスレッドが変わるが、Langfuse の `@observe` は
コンテキスト変数（`contextvars`）でトレース ID を伝播するため問題ない。

各並列ノードが個別のトレースキー（`_trace_f2`, `_trace_f3_own` 等）に検索ログを書き込み、
`synthesis_node` で `retrieval_trace` としてまとめて API レスポンスに含める。

---

## 6. 既知の制約・注意事項

### 6-1. InMemorySessionService はプロセス内メモリのみ

複数 uvicorn ワーカーで起動した場合、セッションはプロセスをまたがない。
PoC（シングルプロセス）では問題ないが、本番で水平スケールする場合は
`FirestoreSessionService` 等の永続ストアへの差し替えが必要。

### 6-2. similar_work_node はスタブ（データ未入手）

Tool3（類似工事データ）は `load_similar_work()` が空リストを返すスタブ実装。
データ入手後は `knowledge_loader.load_similar_work()` の内部実装を差し替えるだけでよい。
`agents.py` の `similar_work_node()` 関数自体は変更不要。

### 6-3. reactor_type フィルタは未有効化

F3 ナレッジの `reactor_type` による絞り込みは、Vertex AI Search の struct_data スキーマに
`reactor_type` カラムが追加されてから有効化する。現在はクエリパラメータとして受け取るが
`knowledge_loader.py` 内で無視されている（TODO コメント済み）。

### 6-4. state_delta の挙動（ADK 2.0 破壊的変更）

ADK 2.0 では `runner.run_async()` の `state_delta` は FunctionNode の実行後イベントに
適用される仕様に変更された。**実行前の入力値注入に `state_delta` は使えない。**

初期値は必ず `create_session(state=initial_state)` で注入すること。

### 6-5. Session State の型制約

ADK の Session State は JSON 直列化可能な型のみ格納できる。
以下の型変換ルールを遵守すること。

| Python 型 | State 格納形式 |
|---|---|
| `set[str]` | `list[str]` に変換して格納 |
| Pydantic モデル | `.model_dump()` で dict に変換 |
| `datetime` | `.isoformat()` で str に変換 |

### 6-6. ADK 2.0 はGA（2026-05-19 リリース）だが比較的新しい

v2.0.0 は 2026-05-19 の GA リリース。本 PoC はリリース直後から採用している。
ライブラリ自体のバグやドキュメント不足が残っている可能性があるため、
問題が発生した場合は以下を参照すること。

- GitHub Issues: https://github.com/google/adk-python/issues
- リリースノート: https://github.com/google/adk-python/releases
- ADK サンプル: https://github.com/google/adk-samples

---

## 7. テスト戦略

### 7-1. 単体テストでの ADK Workflow のモック

E2E テスト（`test_review_e2e.py`）では `adk/agents.py` の `call_gemini` を
`unittest.mock.patch` でモックしている。

```python
_GEMINI_PATH = "apps.backend.app.agents.reviewer.adk.agents.call_gemini"
```

ADK Workflow 自体はモックせず、実際に `run_workflow()` を通じて実行される
（knowledge_loader は Firestore/Vertex AI Search の呼び出しをモックで代替）。

### 7-2. 並列ノードのテスト

各 FunctionNode 関数（`f2_knowledge_node` 等）は `ctx.state` を読み書きするだけの
副作用なし関数のため、`Context` を直接 mock して単体テスト可能。

---

## 8. 本番移行時の変更箇所

| 変更内容 | 対象ファイル | 変更量 |
|---|---|---|
| Firebase Auth の JWT 検証 | `review.py`（`Depends(get_current_user)` を追加） | 1行 |
| caller_role を JWT から取得 | `review.py`（`caller_role=user["role"]` に変更） | 1行 |
| セッションストアの永続化 | `runner.py`（`InMemorySessionService` を差し替え） | 数行 |
| Vertex AI Search フィルタ有効化 | `knowledge_loader.py`（reactor_type フィルタの TODO を解除） | 数行 |
| knowledge_loader.py の変更 | なし（I/F は全 Phase 不変） | 0行 |
| ADK Workflow 構造の変更 | なし（Phase 3 拡張時のみ） | 0行 |
