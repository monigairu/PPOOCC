# NuRO 事前レビュー Phase 1 実装まとめ

最終更新：2026-05-18（Phase2着手前の状態を反映）

-----

## システム全体の流れ

### 電力会社側（/ 画面）

1. 資料ファイルをアップロード
1. AIが自動で転記
1. Excelに書き込み
1. Firestoreにセッション保存（session_id発行）

### NuRO側（/review 画面）

1. 未レビューのセッション一覧を表示
1. レビューしたいセッションを選択
1. 「レビュー開始」ボタンを押す
1. AIが5つのToolを固定順で実行し指摘を生成
1. NuROが指摘を確認・承諾 / 棄却

### 画面ルーティング全体像

```
/              → 電力向け（様式自動作成）← 実装済み
/review        → NuRO向け（事前レビュー）← Phase1完了
/self-review   → 電力向け（セルフレビュー）← 事前レビュー完成後に別途開発
```

-----

## ファイル構成

```
nuro-ai-platform/
├── apps/
│   ├── backend/app/
│   │   ├── api/
│   │   │   ├── main.py                   サーバー起動・全ルート登録
│   │   │   ├── models.py                 データの型定義（設計図）
│   │   │   └── routes/
│   │   │       ├── upload.py             /api/upload（転記機能）
│   │   │       ├── review.py             /api/review（レビュー機能）
│   │   │       ├── chat.py               /api/chat（根拠チャット）
│   │   │       └── template.py           /api/template（様式レイアウト取得）
│   │   ├── agents/
│   │   │   ├── data_extractor/           資料からデータ抽出（既存）
│   │   │   └── reviewer/
│   │   │       ├── knowledge_loader.py   ナレッジ読み込み（Phase2でVertex AI Search差し替え予定）
│   │   │       └── reviewer_agent.py     AIレビューの本体
│   │   └── core/
│   │       ├── ai_client.py              Gemini呼び出し窓口
│   │       ├── firestore_client.py       Firestore接続窓口
│   │       └── frame_config_loader.py    YAML様式定義の読み込み
│   └── frontend/src/
│       ├── App.jsx                       電力向け画面（/）
│       ├── main.jsx                      画面のルーティング
│       └── pages/
│           └── ReviewPage.jsx            NuRO向けレビュー画面（/review）
└── data/knowledge/
    ├── F2_knowledge.xlsx                 NuRO内部の過去知見
    ├── F3_knowledge.xlsx                 電力ごとの問合せ履歴
    └── schema/                           Excelの列構造定義（YAML）
```

-----

## データモデル（models.py）

### 主要クラス

|クラス名            |役割                |主なフィールド                                                                                         |
|----------------|--------------------|------------------------------------------------------------------------------------------------------|
|ReviewItem      |1件の指摘事項        |item_id, field_name, cell_address, severity, comment, evidence, knowledge_source                      |
|ReviewRequest   |レビュー開始リクエスト|session_id, utility_name, sheet_name, frame_name                                                      |
|ReviewResponse  |レビュー結果レスポンス|review_id, review_items, summary, reviewed_at, mappings                                               |
|FeedbackRequest |承諾/棄却リクエスト  |item_id, decision（"accept"/"reject"）, comment                                                        |
|FeedbackResponse|承諾/棄却結果        |status（"saved"/"discarded"）                                                                          |
|SessionSummary  |セッション一覧の1行分|session_id, utility_name, reviewed                                                                    |

### ReviewItem の全フィールド

```python
ReviewItem:
  item_id: str          # 例："review_001"
  field_name: str       # 例："費用低減策"
  cell_address: str     # 例："K22"
  severity: str         # "要確認" or "AIからの指摘"
  comment: str          # 指摘内容
  evidence: str         # 根拠（ナレッジのIDや内容 or "AI判断（ナレッジ参照なし）"）
  knowledge_source: str # "F2" / "F3" / "類似工事" / "補足資料" / "計画差分" / "AI知見"
```

### knowledge_source の値

|値       |意味                                  |
|--------|--------------------------------------|
|"F2"    |NuRO内部の知見から見つけた根拠         |
|"F3"    |電力別問合せ履歴から見つけた根拠       |
|"類似工事"|炉型・工事種別の類似事例（Phase2以降）|
|"補足資料"|写真・図面情報（Phase3以降）          |
|"計画差分"|G列とK列を比較して見つけた差分       |
|"AI知見" |ナレッジがなくAIが自分で判断した場合   |

### severity の値とUIの色分け

|値           |意味                              |色  |
|------------|----------------------------------|-----|
|"要確認"     |ナレッジなし・AIが自主判断した指摘|黄   |
|"AIからの指摘"|ナレッジあり・根拠に基づく指摘    |赤   |
|（確認済み）  |NuROが承諾/棄却を完了した状態     |緑   |

-----

## APIエンドポイント一覧（review.py）

### POST /api/review — レビュー実行

リクエスト：`session_id`, `utility_name`, `sheet_name`, `frame_name`
レスポンス：`review_id`, `review_items`, `summary`, `reviewed_at`, `mappings`

**処理の流れ：**

1. session_id で Firestore からセッション情報（mappings）を取得
1. `reviewer_agent.run_review()` を呼び出してAIレビューを実行
1. 結果（指摘リスト）を Firestore に保存
1. 指摘リスト + mappings をフロントエンドに返す

### POST /api/review/{review_id}/feedback — 承諾/棄却

リクエスト：`item_id`, `decision("accept"/"reject")`, `comment`
レスポンス：`status("saved"/"discarded")`

- `decision="accept"` → Firestore にフィードバックを ArrayUnion で追加
- `decision="reject"` → Firestore には保存しない（その場で破棄）
- どちらの場合も `review_stats` コレクションに集計（Phase2移行判断用）

### DELETE /api/review/{review_id}/feedback/{item_id} — 取り消し

feedbacks リストから item_id が一致するものを除外して上書き

### GET /api/review/sessions — セッション一覧

`reviewed=False`（未レビュー）のセッションを最新順で50件返す

### GET /api/review/stats — Phase2移行判断指標

```json
{
  "total_accepted": 15,
  "total_rejected": 8,
  "rejection_rate": 0.347,
  "phase2_trigger": false,
  "phase2_reasons": []
}
```

棄却率が50%を超えると `phase2_trigger: true` になる

-----

## knowledge_loader.py — ナレッジ読み込み

> **Phase設計方針**：内部実装（検索技術）はPhaseごとに差し替えるが、
> 引数・戻り値（I/F）は全Phase通じて変更しない。
> → `reviewer_agent.py`・APIエンドポイント・フロントエンドへの影響なし。

### 関数一覧（I/Fは全Phase共通）

```python
def load_f2(caller_role, fee_type=None, limit=30) -> list[dict]
def load_f3(caller_role, utility_name=None, reactor_type=None,
            fee_type=None, sheet_name=None, limit=50) -> list[dict]
def load_similar_work(caller_role, reactor_type=None,
                      fee_type=None, limit=20) -> list[dict]
def load_supplement(caller_role, utility_name=None,
                    fee_type=None, limit=20) -> list[dict]
# Tool5（計画・実績差分）は detect_plan_diff() として別実装（ルールベース・全Phase変更なし）
```

### Phase1の実装内容（構造化フィルタ型）

1. `data/knowledge/schema/` の `f3_*_schema.yaml` を全て自動検出
1. 各スキーマに書いてある `excel_file` と `excel_sheet` でExcelを開く
1. `header=None` で読み込む（ヘッダー行は自動検出しない）
1. セル結合で生じた空欄を **ffill（前方補完）** で埋める
1. 固定列を列文字（B, C…）→ 列インデックス（1, 2…）に変換
1. QA繰り返し列（NuRO確認 ↔ 電力回答）を **縦持ちに変換**
1. フィルタリングして返す

### 縦持ち変換のイメージ

**変換前（横持ち：Excelの1行）**

|ID |NuRO確認(1回)|電力回答(1回)|NuRO確認(2回)|電力回答(2回)|
|---|----------|--------|----------|--------|
|001|工数が不足     |修正します   |了解しました    |はい      |

**変換後（縦持ち：4行）**

|ID    |発言者     |内容    |
|------|--------|------|
|001_01|nuro    |工数が不足 |
|001_01|denryoku|修正します |
|001_02|nuro    |了解しました|
|001_02|denryoku|はい    |

### Phase1の既知の限界（コードにコメント済み）

|#|限界|解決予定|
|--|--|--|
|①|同義語・表記ゆれに対応できない（「費用低減」と「コスト削減」が別扱い）|Phase2|
|②|補足資料の写真・図面情報が使えない（スタブのみ）|Phase3|
|③|reactor_type（炉型）の絞り込みが機能しない（F3スキーマに列なし）|Phase2|
|④|ナレッジ増加時に取りこぼしリスクがある|Phase2のRerankingで対応|

### 権限制御（caller_role）

|項目|PoC|本番|
|---|---|---|
|認証|なし（URLで分けるだけ）|Firebase Authentication|
|caller_roleの取得元|エンドポイントで"NuRO"を固定|検証済みJWTのclaims|
|なりすまし防止|なし|JWTから取得のため偽れない|


> 本番移行時は FastAPI の `Depends` を1行追加してJWTから取得するだけ。
> `knowledge_loader.py` 自体の変更は不要。

-----

## reviewer_agent.py — AIレビューの本体

### run_review() — 5つのToolを固定順で実行

> **設計方針**：現在は固定順で全Tool実行（Agentic RAGへの本格移行はPoC後に判断）

|Tool  |データ種別|処理内容                                       |Phase1実装状況|
|------|---------|----------------------------------------------|-------------|
|Tool1 |F2ナレッジ |NuRO内部知見を取得（limit=20）                  |構造化フィルタ  |
|Tool2 |F3ナレッジ |自社の過去指摘事例を取得（limit=30）             |構造化フィルタ  |
|Tool3 |F3ナレッジ |全社の類似事例を取得（limit=30）                 |構造化フィルタ  |
|Tool4 |補足資料  |補足資料テキストを取得                           |スタブ（空リスト）|
|Tool5 |計画・実績差分|G列（計画）とK列（実績）を比較（ルールベース）  |実装済み・全Phase変更なし|

→ 全Toolの結果をGeminiに渡してレビューを生成

> **注意**：Tool番号はPhase2着手前に整理予定（F2・F3・類似工事・補足資料・差分の順番に入れ替え）

### detect_plan_diff() — 計画と実績の差分を検出

1. mappings から「計画実績区分」を取り出す
1. 「実績」でない場合は即返却（計画提出時は差分チェック不要 → 空リストを返す）
1. `MRC1.yaml` の `plan_actual` セクションからG/K列のペアを取得
1. mappings から値を取り出して比較
1. **10%以上の差がある場合に**差分情報を返す

### _build_prompt() — プロンプトに含まれる内容

- レビュー対象の転記結果（JSON形式）
- Tool1〜5の検索結果（JSON形式）
- 指示内容（何を確認してほしいか）
- **ハルシネーション防止の制約**：法令条文・数値基準を根拠にした指摘は行わない
  - ナレッジなし時はGemini汎用知識で補完するが、`severity="要確認"`・`knowledge_source="AI知見"` で明示する

### AIモデル

|用途|モデル|
|---|---|
|レビュー指摘生成（Phase1・2）|Gemini 2.5以上（現在Gemini 3 Auto使用中）|
|マルチモーダル前処理（Phase3）|Gemini 3以上|

-----

## Firestoreデータ構造

```
sessions/{session_id}/
  session_id, utility_name, frame_name, sheet_name
  mappings: list[dict]
  created_at, reviewed: bool

  review_results/{review_id}/
    review_items, summary, reviewed_at
    feedbacks: list[dict]  # 承諾済みのみ保存（棄却はDBに保存しない）

review_stats/  # Phase2移行判断用の集計
  total_accepted, total_rejected, rejection_rate
```

-----

## フロントエンド（ReviewPage.jsx）

### 3カラム構成

```
┌─────────────┬────────────────────────┬────────────────────┐
│ 左パネル    │ 中央パネル             │ 右パネル           │
│             │                        │                    │
│ セッション  │ 様式グリッド           │ AI指摘一覧タブ     │
│ 一覧        │ （指摘セルを赤ハイライト）│ ・承諾ボタン      │
│             │  クリックで下部に       │ ・棄却ボタン       │
│ レビュー    │  F3プレビュー展開       │ ・取り消しボタン   │
│ 開始ボタン  │                        │                    │
│             │ 下部ドロワー           │ AIチャットタブ     │
│             │ （選択した指摘の詳細） │                    │
└─────────────┴────────────────────────┴────────────────────┘
```

デザイン：本番NuROサイトのUIに準拠（ティール系グリーン基調）

### 主要な状態変数（useState）

|変数名            |何を保持するか          |
|---------------|----------------------|
|sessions       |未レビューのセッション一覧|
|selectedSession|選択中のセッション       |
|reviewItems    |AIが生成した指摘リスト   |
|sessionMappings|転記結果（グリッド表示用）|
|feedbackMap    |各指摘の承諾/棄却状態    |

### 楽観的更新（handleUndo）

APIの応答を待たずに先にUIを更新する手法。取り消し操作後すぐ画面が変わるため体感速度が向上する。

-----

## データの流れ（まとめ）

### 転記時

```
資料アップロード
    → data_extractor_agent.extract_data()   AIが資料からデータを抽出
    → form_generation_pipeline.generate()   Excelに書き込み
    → _save_session_to_firestore()          セッション情報を保存
    → session_id を返す
```

### レビュー時

```
POST /api/review (session_id, utility_name, sheet_name, frame_name)
    → Firestoreからmappings取得
    → knowledge_loader.load_f2()       F2 Excelからナレッジ検索  ← Phase2でVertex AI Searchに差し替え
    → knowledge_loader.load_f3()       F3 Excelからナレッジ検索  ← Phase2でVertex AI Searchに差し替え
    → knowledge_loader.load_supplement() 補足資料取得（現在スタブ）← Phase3で実装
    → detect_plan_diff()               G列とK列の差分計算（ルールベース・変更なし）
    → _build_prompt()                  ナレッジ+転記結果をプロンプトに組み立て
    → call_gemini()                    Geminiに指摘を生成させる
    → _parse_review_response()         JSONをPythonオブジェクトに変換
    → Firestoreに保存
    → フロントエンドにreview_items + mappings を返す
```

-----

## Phase2着手前の準備事項

```
① Toolの番号整理（Claude Codeへ依頼）
   現在のTool1〜5をF2・F3・類似工事・補足資料・差分の順番に整理
   ※ reviewer_agent.py・プロンプト文字列のハードコード箇所を事前に確認すること

② GCPプロジェクトでVertex AI Searchを有効化（先行して着手）
   F2・F3ナレッジExcelをVertex AI Searchのコーパスに投入するスクリプト作成

③ knowledge_loader.pyの内部実装をVertex AI Searchに差し替え
   ※ I/F（引数・戻り値）は変えない
```

-----

## 用語集

|用語                |説明                                                                     |
|------------------|-------------------------------------------------------------------------|
|FastAPI           |Python でWebサーバーを作るライブラリ                                       |
|ルーター              |「このURLが来たらこの処理をする」という振り分け担当                           |
|Pydantic BaseModel|データの「型」を定義するクラス                                              |
|Firestore         |Googleが提供するNoSQLデータベース。JSONのような形式でデータを保存する          |
|session_id        |転記1回ごとに発行されるID（図書館の貸出番号のようなもの）                      |
|caller_role       |「誰が呼んでいるか」を表す引数。現在は"NuRO"固定・本番ではJWTから取得          |
|遅延初期化            |プログラム起動時にすぐ接続せず、初めて使われるときに接続する方式                 |
|ffill（前方補完）     |セル結合されて空欄になっている部分に、上の行の値をコピーして埋めること            |
|縦持ち変換            |Excelで横に並んでいるQAの往復を縦に1行ずつに変換すること                      |
|RAG               |「検索して補強して生成する」手法。関連情報を事前に検索してAIのプロンプトに添付することで精度を高める|
|Agentic RAG       |固定順ではなくAIがToolの実行順・実行有無を判断するRAG。現在は固定順で実装        |
|ハルシネーション        |AIが存在しない法律や数値を「あるかのように」作り話してしまう現象                 |
|楽観的更新            |APIの応答を待たずに先にUIを更新する手法                                      |
|ArrayUnion        |Firestoreの配列に要素を追加するときの書き方                                  |
|Vertex AI Search  |GoogleのRAG向け検索基盤。BM25+ベクトル検索を自動ブレンドするハイブリッド検索    |
|Reranking         |検索結果の上位N件を再スコアリングして精度を上げる処理                          |
|reactor_type      |炉型（例：BWR・PWRなど）。Phase2でVertex AI Searchのフィルタとして使用予定    |