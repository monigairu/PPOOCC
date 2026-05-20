# AIによる様式事前レビュー機能 詳細要件書

作成日：2026-05-14
最終更新：2026-05-17
対象：PPOOCC PoC（事前レビュー機能追加）
立て付け：本番を想定した技術検証としてのPoC

---

## 1. 機能概要

電力会社が提出した様式案に対し、NuRO担当者が手動チェックを行う前に
AIが自動でレビューを実施することで、NuROの確認作業工数を削減する。

```
電力：様式自動作成①（実装済み）
  → 転記完了 → Firestoreにセッション保存
    → NuRO：事前レビュー画面でセッションを選択してレビュー開始
      → AI：複数観点で並列検索・指摘生成
        → NuRO：指摘を確認しながら手動チェック
```

対象ユーザー：NuRO担当者

```
/              → 電力向け（様式自動作成①）← 実装済み
/review        → NuRO向け（事前レビュー）← 実装中
/self-review   → 電力向け（セルフレビュー）← 別途開発（事前レビュー完成後）
```

---

## 2. インプット情報とナレッジ（5種類）

画像のスライド（セルフレビューと事前レビューの立て付け整理）より、
事前レビューで参照するデータは以下の5種類。

| Tool | データ種別 | 内容 | NuROのアクセス範囲 |
|---|---|---|---|
| Tool1 | F2ナレッジ | NuRO内共有の問合せナレッジ | 全件参照可 |
| Tool2 | F3ナレッジ | 電力個別の問合せナレッジ | 全電力会社分参照可 |
| Tool3 | 類似工事データ | 炉型・工事種別の類似事例 | 全電力会社分参照可 |
| Tool4 | 補足資料 | 写真含むExcel・PPTX | 全電力会社分参照可 |
| Tool5 | 計画・実績差分 | 計画値vs実績値の乖離 | 同一Excel内で比較 |

### 補足資料の実態

- 解体状況図（PPTX）：建屋平面図に工事進捗を色で重ね書き。工事ID・工事名・作業エリア名のテキストと位置・色情報の図面データが混在。
- 補足資料Excel：1シート1工事で「撤去前→撤去後」の写真2枚＋工事名テキスト。

### 計画値と実績値の差分（Tool5・実装済み）

実績提出の場合のみ差分検出。計画提出の場合は空リストを返す。
MRC1のplan_actualフィールド（G列=計画、K列=実績）を比較。
数値差異が10%以上の場合に指摘対象とする。

---

## 3. アウトプット（指摘事項）

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

NuROの操作：

- 承諾：Firestoreに保存
- 棄却：セッション内で破棄（DBに保存しない）
- 承諾・棄却の取り消し：DELETE /api/review/{review_id}/feedback/{item_id}（実装済み）
- 棄却情報のフィードバック学習はPoC対象外

---

## 4. RAG設計

### 4-1. 基本方針：Agentic RAG（観点別Tool群による固定順実行）

Agentic RAGが親の構造として全Phaseで変わらない。
新しいナレッジソースはToolを追加するだけで対応できる。
各Toolの内部実装（検索技術）はPhaseごとに高度化する。

### 4-2. Phase別の実装内容

**Phase1（完了）：構造化フィルタ型RAG**

```
目的：動くものを作ってレビューの基本動作を確認する

Tool1（F2）   → Excelを読んで費目でフィルタ
Tool2（F3）   → Excelを読んで電力会社・費目でフィルタ
Tool3（類似工事）→ スコープ外
Tool4（補足資料）→ スタブ実装のみ
Tool5（差分）  → G列とK列の数値比較（ルールベース・変更なし）

検索技術：ベクトル検索なし・構造化フィルタのみ
補完：ナレッジなし時はGemini汎用知識で補完
      ハルシネーション防止：法令条文・数値基準を根拠にした指摘は行わない
      ナレッジなし指摘は severity="要確認"・knowledge_source="AI知見" で明示
```

**Phase2（PoCとして必ず実施）：ハイブリッド検索RAG**

```
目的：同義語・表記ゆれに対応して検索精度を上げる
      「費用低減」と「コスト削減」が同じ意味として検索できるようになる

Tool1（F2）   → Vertex AI Search（ハイブリッド+Reranking）
Tool2（F3）   → Vertex AI Search（ハイブリッド+Reranking）
Tool3（類似工事）→ Vertex AI Search（データ入手後）
Tool4（補足資料）→ テキスト部分のみVertex AI Search
Tool5（差分）  → 変更なし（ルールベースのまま）

検索技術：
  Vertex AI Search（旧Discovery Engine）
  → 内部でBM25+ベクトル検索を自動ブレンド
  → RerankingConfigを追加して上位N件に絞る

精度が悪かった場合の変更容易性：
  knowledge_loader.pyの内部実装のみ変更すれば良い
  reviewer_agent.py・APIエンドポイント・フロントエンドは変更不要
  Rerankingの追加・削除も knowledge_loader.py 内の設定変更のみ
```

**Phase3（PoCとして実施）：マルチモーダルRAG**

```
目的：写真・図面の情報もレビューに使えるようにする

Tool4（補足資料）を拡張：

【データをDBに入れる時・一度だけの前処理】
補足資料Excel・PPTX
  ↓ openpyxlで画像を抽出
  ↓ 画像の近くにあるテキスト情報も取得（例：「撤去後」というセル名）
  ↓ Gemini 3に渡す
    「撤去後と書かれた枠の写真です。工事状態を説明してください」
  ↓ キャプション生成
    「PPパネルが完全に撤去されており工事完了状態」
  ↓ キャプションをVertex AI Searchに投入（ベクトル化して保存）

【レビュー実行時・毎回】
Vertex AI Searchでキャプションを検索
→ 関連する補足資料の情報を取得してGeminiに渡す

精度が不十分な場合の第2選択：
  Vertex AI Multimodal Embeddings（画像そのものをベクトル化）
  キャプションとEmbeddingを組み合わせるハイブリッドアプローチ

Document AIは使用しない：
  理由：写真の内容・図面の色情報を理解できないため
  Gemini 3マルチモーダルで代替する
```

### 4-3. 使用AIモデル

```
レビュー指摘生成：Gemini 2.5以上（現在Gemini 3 Auto使用中）
マルチモーダル前処理（Phase3）：Gemini 3以上
```

### 4-4. Phase1の限界（認識済み・コードにコメント済み）

```
① 同義語・表記ゆれに対応できない → Phase2で解決
② 補足資料の写真・図面情報が使えない → Phase3で解決
③ reactor_type（炉型）の絞り込みが機能しない → Phase2で解決
④ ナレッジ増加時の取りこぼしリスク → Phase2のRerankingで解決
```

### 4-5. Agentic RAGへの移行トリガー

現在は固定順で全Tool実行。以下の条件で本格的なAgent化を検討する。

| トリガー | 判断基準 |
|---|---|
| 指摘の棄却率が高い | NuRO担当者の棄却率が50%超が継続 |
| 見落としが頻発 | 「なぜこの指摘が出ないのか」が月10件超 |
| ナレッジ量の爆発 | F3ナレッジが1万件超 |
| 観点の複雑化 | 様式の内容によって調べる観点が動的に変わるようになった |

---

## 5. Tool別の検索技術まとめ

| Tool | データ | Phase1 | Phase2 | Phase3 |
|---|---|---|---|---|
| Tool1 | F2ナレッジ | 構造化フィルタ | Vertex AI Search（ハイブリッド+Reranking） | 変更なし |
| Tool2 | F3ナレッジ | 構造化フィルタ | Vertex AI Search（ハイブリッド+Reranking） | 変更なし |
| Tool3 | 類似工事データ | スコープ外 | Vertex AI Search（データ入手後） | 変更なし |
| Tool4 | 補足資料 | スタブのみ | テキスト部分のみVertex AI Search | Gemini 3前処理→Vertex AI Search |
| Tool5 | 計画・実績差分 | ルールベース | 変更なし | 変更なし |

---

## 6. knowledge_loader.py 設計

```python
"""
設計方針：
  各Phaseでknowledge_loader.pyの内部実装のみ変更する
  I/F（引数・戻り値）は全Phase通じて変更しない
  → reviewer_agent.py・APIエンドポイント・フロントエンドへの影響なし

Phase1（現在）：Excelから直接読み込み・構造化フィルタ
Phase2（PoC）：Vertex AI Search（ハイブリッド+Reranking）
Phase3（PoC）：Gemini 3マルチモーダル前処理→Vertex AI Search

本番移行時の注意：
  caller_roleは現在エンドポイントで"NuRO"固定
  本番ではFastAPIのDependsを1行追加してJWTから取得するだけ
  このファイルの変更は不要
"""

def load_f2(caller_role, fee_type=None, limit=30) -> list[dict]
def load_f3(caller_role, utility_name=None, reactor_type=None,
            fee_type=None, sheet_name=None, limit=50) -> list[dict]
def load_similar_work(caller_role, reactor_type=None,
                      fee_type=None, limit=20) -> list[dict]
def load_supplement(caller_role, utility_name=None,
                    fee_type=None, limit=20) -> list[dict]
# Tool5（計画・実績差分）はdetect_plan_diff()として別実装（ルールベース）
```

---

## 7. 権限制御設計

AIではなくDB層でフィルタリングを完結させる。

| 項目 | PoC | 本番 |
|---|---|---|
| 認証 | なし（URLで分けるだけ） | Firebase Authentication |
| caller_roleの取得元 | エンドポイントで"NuRO"を固定 | 検証済みJWTのclaims |
| なりすまし防止 | なし | JWTから取得のため偽れない |

---

## 8. APIエンドポイント（実装済み）

```
POST /api/review
  リクエスト：session_id, utility_name, sheet_name, frame_name
  レスポンス：review_id, review_items, summary, reviewed_at, mappings

POST /api/review/{review_id}/feedback
  リクエスト：item_id, decision("accept"/"reject"), comment
  レスポンス：status("saved"/"discarded")

DELETE /api/review/{review_id}/feedback/{item_id}
  承諾・棄却の取り消し

GET /api/review/sessions
  レスポンス：reviewed=falseのセッション一覧
```

---

## 9. Firestoreデータ構造

```
sessions/{session_id}/
  session_id, utility_name, frame_name, sheet_name
  mappings: list[dict]
  created_at, reviewed: bool

  review_results/{review_id}/
    review_items, summary, reviewed_at
    feedbacks: list[dict]（承諾済みのみ）
```

---

## 10. フロントエンド（/review）

3パネル構成：
- 左：セッション一覧・レビュー開始ボタン
- 中央：様式プレビュー（指摘セルを赤ハイライト・クリックで下部にF3プレビュー展開）
- 右：指摘事項一覧・承諾ボタン・棄却ボタン・AIチャット

指摘の色分け：要確認=黄・AIからの指摘=赤・確認済み=緑
デザイン：本番NuROサイトのUIに準拠（ティール系グリーン基調）

---

## 11. 実装進捗

最終更新：2026-05-19

| Step | 内容 | 状態 | 完了度 |
|---|---|---|---|
| Phase1 | 構造化フィルタ型RAG・E2E検証 | 完了 | 100% |
| Phase2 | Vertex AI Search・ハイブリッド検索 | 完了 | 100% |
| Phase3 | Gemini 3マルチモーダル前処理 | 未着手 | 0% |

### Phase2 完了内容

```
① Toolの番号整理
   Tool2をF3自社（2a）・F3他社（2b）に分割
   Tool3（類似工事）・Tool4（補足資料）・Tool5（差分）の順番を整理

② GCPプロジェクトでVertex AI Searchを有効化
   create_datastores.py → nuro-f2-knowledge / nuro-f3-knowledge を作成済み
   ingest_knowledge.py → F2・F3ナレッジをVertexAI Searchに投入済み
   .env → データストアID・エンジンID設定済み

③ knowledge_loader.pyの内部実装をVertex AI Searchに差し替え完了
   I/F（引数・戻り値）は変更なし
   reviewer_agent.py・APIエンドポイント・フロントエンドへの影響なし

④ Phase2で追加したもの
   _excel_reader.py       データ投入用Excel読み込みモジュール（新規）
   langfuse_client.py     RAGトレーシング基盤（新規）
   docker-compose.langfuse.yml  Langfuseローカル環境（新規）
   /api/review/stats      Phase2移行判断指標エンドポイント（新規）
   retrieval_trace        各ToolのRAG取得ログをレスポンスに追加
```

### Phase2 残存制約（要件通り）

```
① load_similar_work() はデータ未入手のためスタブ（空リスト）
② reactor_type フィルタは struct_data 拡張後に有効化（TODOコメント済み）
③ load_supplement() は Excelテキスト読み込み（Vertex AI Search 化は Phase3 で実施）
```

### Phase3 着手前の準備

```
① 補足資料Excel・PPTX の収集
   data/knowledge/supplement/ に配置する

② Gemini 3 でキャプション生成スクリプトの作成
   openpyxl で画像抽出 → Gemini 3 に渡してキャプション生成 → Vertex AI Search に投入

③ knowledge_loader.py の load_supplement() 内部実装を差し替え
   ※ I/F（引数・戻り値）は変えない
```

---

## 12. 未確定事項

| 項目 | 状況 | 対応方針 |
|---|---|---|
| 類似工事データ（Tool3） | 討議中 | データ入手後にVertex AI Searchに投入 |
| reactor_typeの絞り込み | F3スキーマに列なし | Phase2でVertex AI Searchのフィルタで対応 |
| セルフレビュー（/self-review） | 事前レビュー完成後に別途開発 | review_modeパラメータで分岐する設計 |

---

## 13. 事前レビュー vs セルフレビューのデータアクセス範囲

スライドの比較表より：

| ナレッジ | 事前レビュー（NuRO） | セルフレビュー（電力） |
|---|---|---|
| F1（全電力共有） | × 参照しない | × 参照しない |
| F2（NuRO内共有） | ○ 全件参照可 | × 参照不可 |
| F3（電力個別） | ○ 全電力会社分 | △ 自社分のみ |
| 類似工事データ | ○ 全電力会社分 | △ 自社分のみ |
| 補足資料 | ○ 全電力会社分 | △ 自社分のみ |
| 計画時の様式データ | ○ | △ |
