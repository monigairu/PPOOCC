# AIによる様式事前レビュー機能 詳細要件書

作成日：2026-05-14
最終更新：2026-07-02（数値チェック2種の区別を明記・参照/表現の整合修正）
対象：事前レビュー機能追加
立て付け：本番を想定した技術検証としてのPoC

---

## 0. 確定事項（最新仕様・PoC決定の反映）★本書の正本

> **読み方**：本§0 が現時点の確定仕様。以降の §1〜 は原本（PoC前・2026-05時点）で、
> §0 と差異がある箇所は **§0 を優先**する。検証の根拠・設計判断の詳細・実装バックログは
> `RAG_VERIFICATION.md`（HOW/Proof）を参照。
>
> **⚠️ §0 は確定"仕様（目標）"であり、"実装済み"とは限らない**。実装状況は **§11-2（到達点サマリ）**／
> **`RAG_VERIFICATION.md §1（実装済）・§2（未実装）`** で必ず確認すること。
> （**§0-7 の BigQuery/平坦化/ver.5.3 は実装済み🟦**（2026-07-02・Step1）。
> 特に **§0-3 後半の計画/実績シート分岐・Reranking は未実装🔲**）

### 0-1. スコープ（PoCで対象とするTool）
- **対象**：Tool1=F2 / Tool2a=F3自社 / Tool2b=F3他社 / Tool5=計画実績差分（ルール）＋ レビュー観点。
  - ※Tool5は**枠組みは稼働するが数値の計画/実績差分は現状出ない**（数値がMRC2へ移管・MRC2に計画/実績ペア未定義＝課題①b待ち。テキスト差分は可）。
- **PoC範囲外**：**Tool3（類似工事データ）・Tool4（補足資料＝写真/図面のマルチモーダル＝Phase3）**。
  → 本書 §2・§4・§5・§14（Phase3詳細）の Tool3/Tool4・Phase3 記述は**今回スコープ外**として読む。

### 0-2. レビュー生成の確定方式
- **grounding**：F2/F3 の過去事例を根拠にした指摘（`knowledge_source`＋`evidence=[F3all#N]` 等）。
- **誤grounding防止**：費目トークンの**関連性ガード**＋プロンプトの関連性指示（無関係データを根拠化しない）。
- **観点**：`data/review_criteria/{frame}_{sheet}.yaml`（宣言的・資料/カテゴリに非依存）。
- **横断原則**：チェックの拠り所は「**様式定義（config）＋普遍的算術**」。特定費目/見積書構造をハードコードしない。
  `run_review` / `knowledge_loader` の I/F は不変を維持
  （**不変の定義**：既存引数の意味・戻り値構造を変えない。デフォルト値付きオプション引数の追加は可）。

### 0-3. 検索（リトリーバル）の確定仕様
- バックエンド：**Agent Search（旧 Vertex AI Search／Discovery Engine）** のハイブリッド検索（BM25+ベクトル）。
- クエリ：申請自身の **費目＋工事件名**（観点語はハードコードしない）。
- フィルタ：**会社名は正規化**（株式会社等を吸収）、**炉型(BWR/PWR)** は struct_data＋後段フィルタで適用。
  - **炉型の出所（2026-07-03確定）**：様式に炉型の列は**持たない**（ver5.3列定義に炉型なし・電力会社に手動維持させない）。
    **該当発電所から導出**する（`data/knowledge/schema/plant_reactor_map.yaml`＝ドメイン知識のconfig・号機で異なる例外は「発電所/号機」キーで上書き）。
- **Reranking**：Agent Search の **Ranking API（semantic-ranker）を採用方針**（surfacing/精度向上・低コスト）。
- **提出タイミング（計画/実績）で「検索シート」と「レビュー列」を分岐**（🔲未実装・§2）（転記結果 MRC1 の C8＝計画実績区分で判定）：

  | 申請区分(C8) | RAG検索シート | レビュー列 | 追加 |
  |---|---|---|---|
  | **計画** | KNI_1G_01（計画申請時） | **G列＝計画の縦カラム** | — |
  | **実績** | KNI_1G_02（費用請求時） | **K列＝実績の縦カラム** | **計画(G)と大きく差がある項目は差分もレビュー** |

  - 前提：計画は計画列(G)、実績は実績列(K)を縦にレビューし、**検索をかけるシートも区分で別**。
  - **「情報提供時」は別ナレッジのため検索対象外。** 「執行確認時」(KNI_1G_03)も**この計画/実績分岐では未使用**（実績＝費用請求時KNI_1G_02のみ）。
  - **実績の差分レビューは Tool5（`detect_plan_diff`）**で実施（計画G vs 実績K の乖離が**10%以上**の時のみ指摘・§0-6）。
    数値項目は MRC2 へ移管済みのため **MRC2 側で比較**（バックログ課題①b「MRC2計画/実績ペア定義」と連動）。
  - 共通の識別項目（件名・費目・号炉等）は区分に関わらず確認対象。
  - 実装：区分→`submission_timing` の後段フィルタ＋レビュー対象セルの G/K 選別（config駆動・**I/F不変**・measure-first）。

### 0-4. データストアの役割（DB設計）
- **Firestore**＝レビュー中の運用状態（セッション・指摘・採否のリアルタイム・undo・履歴）。
- **Agent Search(F3)**＝検索で根拠を引く本体（RAGの検索エンジン）。承諾ナレッジを次回"検索"に効かせる**還流先**もここ（将来）。
- **BigQuery＝2用途（混同しないこと）**：
  - **① F3知識のデータ置き場**（平坦テーブル）。**PoCで採用**し、**Agent Search がこれを索引**して検索する（§0-7／`RAG_VERIFICATION.md §3-3`）。
  - **② 採否（承諾/棄却）結果の分析・蓄積**（棄却率・観点別傾向・工数削減効果）。**検索はしない**・**将来**。
- **PoCで使うDB＝Firestore＋（F3用の）BigQuery①**。②の分析用途と、承諾ナレッジの還流（→Agent Search）は将来。
- ※「承諾ナレッジを次回"検索"で活かす」先は **Agent Search(F3)**。BigQuery②（分析）とは別の流れ。

### 0-5. フロー・採否
- トリガー：**PoC=NuROが手動起動**（本番は Cloud Functions で簡易自動化）。⑤完了通知は**スコープ外**。
- 採否：承諾/棄却。**PoCの承諾保存は Firestore**（フィードバック学習＝将来）。
- AIチャット：**「なぜこの指摘か（根拠＝該当F3・観点）を説明できる」軽量版**（指摘の自動再作成は求めない）。
- 図解フロー（PoC現状/将来）：`RAG_VERIFICATION.md §3-4`（Mermaid）を参照。

### 0-6. 決定済み・未実装（実装バックログ）
- 数値の妥当性チェック（決定論・**単位は円・許容0**）／転記の**粒度感**チェック（観点）／
  config 移行の転記系パス追従／Ranking API 実装 等は **決定済み・未実装**。詳細は `RAG_VERIFICATION.md §2`。
- **数値チェックは2種類ある（混同しないこと）**：

  | チェック | 目的 | 判定基準 |
  |---|---|---|
  | **数値妥当性チェック＝軽量版「数式破壊検知」**（🔲未実装・2026-07-02確定） | 合計・関数セルの結果を**再計算と突合**し、数式のベタ値上書き・SUM範囲ずれを検出（金額=数量×単価／合計=Σ明細／MRC1総額=MRC2年度総額SUM）。※テンプレ数式が健在なら指摘ゼロが正常（算術は数式で構造保証されるため。チェックの狙いは**数式の破壊・上書き**） | **決定論・単位は円・許容0** |
  | **Tool5 計画/実績差分**（✅実装済み） | 計画(G) vs 実績(K) の**乖離検出**（算術検証ではない） | **数値差異10%以上**で指摘（`_NUMERIC_DIFF_THRESHOLD_RATE`・決定論） |

### 0-7. データI/O・DB設計（2026-06-25 の議論で確定）

> **状態：実装済み🟦（2026-07-02・Step1・マトリクス全PASSで本採用）**。現行経路は
> `Excel(正本) → 平坦化(ver5.3) → BigQuery → Agent Search索引（nuro-f3-bq-knowledge）`。
> 旧 Excel→Agent Search 直接投入（nuro-f3-knowledge）は比較基準として残置・検索には未使用
> （`RAG_VERIFICATION.md §1-10〜12`）。
>
> 用語：**平坦化**＝「KNI_*の横持ち（やりとりが横に伸びる）」を「**1メッセージ＝1行**」の縦持ち
> （出力用シート相当）に変換すること。**正本**＝編集の起点（本物のコピー）。

- **データ形式（R1）**：F3ナレッジは新様式 ver.5.3 の**平坦形式（1メッセージ＝1行）**で構造化。
  列＝ID／メッセージID／起票日／起票者所属G／起票者／参照先ナレッジID／提出タイミング／確認年度／
  **該当発電所／該当プラント／該当費目／該当工事**／該当資料／メッセージ内容。
- **正本（R2）**：**電力会社ごとの F3 Excel（様式）**。非エンジニア（電力／NuRO）がExcelで編集。
  DBは派生。**編集は一方向（Excel→DB）**（DBを人が直接編集しない＝二重管理回避）。
- **データの流れ（R3）**：`Excel(正本) → 平坦化 → BigQuery(置き場) → Agent Search(検索エンジン=索引) → RAG検索`。
  Firestore（レビュー中の運用状態）は別系統。
- **各部品の役割（R4）**：Excel=正本／**BigQuery=データ置き場**／**Agent Search=検索エンジン**／Firestore=運用状態。
- **Excelの置き場・取り込み（R5）**：置き場＝SharePoint（現状）。取り込み＝**PoCは手動**（DL→スクリプト投入）、
  **本番は自動**（SharePoint更新／アップロードをトリガーに Cloud Function で平坦化→DB→索引）。
- **フィードバック還流（R7）**：承諾→DB→次回検索反映は**本番機能**。**PoCは知識ベースを凍結（固定）**して
  検証の再現性を担保（今回データはほぼ固定のため **BigQuery採用OK**）。
- **レビュー結果のExcel出力（R6）**：承諾指摘を該当電力の F3 Excel「やりとり列」へ追記→Excel出力（ナレッジFMT）。
  **実現性は確認済み**（追記＝openpyxlで容易／BigQuery反映＝INSERT/MERGEで容易）だが、**PoCでは凍結＝未実装**
  （同じデータの再レビューで同じ指摘が連なり再現性が崩れるため）。**将来実装**。
- **Reranking（R8）**：Agent Search の Ranking API を採用方針（§0-3）。

> 実装ステップ・検証・採否の根拠は `RAG_VERIFICATION.md §2（バックログ）／§3-3（DB方針）` を正本とする。

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

> ⚠️ **§0-1／§0-3 を優先**：数値項目は MRC2 へ移管済みで、MRC1 に計画/実績ペアは現存しない
> （＝数値の計画/実績差分は現状出ない・課題①b待ち）。閾値10%は確定仕様として維持（§0-6）。

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

**Phase3（~~PoCとして実施~~ → PoC範囲外・§0-1）：マルチモーダルRAG**

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

GET /api/review/stats
  レスポンス：承諾/棄却件数・棄却率（Agentic RAG移行判断の指標・§4-5）
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

## 11. 実装進捗（最新・2026-06-24）

> 旧 Phase1/2/3 区分の進捗表（2026-05-19・Phase3=実装中）は**陳腐化のため廃止**。
> 現在の進捗・課題・残作業の**詳細な正本は `RAG_VERIFICATION.md §1（実装済み）／§2（課題・バックログ）`**。
> 本節はそのサマリ。

### 11-1. PoCスコープの再定義（Phase区分→Tool区分）
- 旧Phase1（構造化フィルタ）→ Phase2（ハイブリッド検索）は **Agent Search 上で完了**し、現行は **Phase2 相当
  （ただし §4-2 Phase2 定義のうち Reranking は未実装**・§0-3 の採用方針）。
- 旧 **Phase3（Tool4 補足資料マルチモーダル）は PoC範囲外**（§0-1）。Tool3（類似工事）も範囲外。
- よって本書の進捗は **Phase進捗でなく「Tool1/2a/2b＋Tool5＋観点」の実装・検証状況**で見る。

### 11-2. 現在の到達点（サマリ）
| 区分 | 状態 |
|---|---|
| 検索（Agent Search ハイブリッド・費目+工事名クエリ・会社名正規化・**炉型フィルタ有効化済**） | ✅ 実装・実データ検証済 |
| grounding（F2/F3根拠の指摘）＋誤grounding防止（関連性ガード）＋指摘統合 | ✅ 実装・検証済 |
| レビュー観点（review_criteria）／Tool5 計画実績差分（現設計に整合） | ✅ 実装済 |
| 検証基盤（verify_rag／eval_review＋gold_expectations／review_annotation） | ✅ 整備済・回帰41 PASS |
| **F3検索基盤（Excel→平坦化ver5.3→BigQuery→Agent Search索引・§0-7）** | ✅ 実装・マトリクス全PASSで本採用（2026-07-02・Step1） |
| **Reranking（Ranking API）** | 🔲 採用方針・**未実装**（§0-3／`RAG_VERIFICATION.md §2`） |
| 数値妥当性チェック／転記の粒度感チェック／MRC2観点／config転記系パス追従 | 🔲 決定・未実装 |
| ゴールド指摘（NuRO正解）の確定／認証（本番） | 🔲 未確定・将来 |
| Tool3 類似工事／Tool4 補足資料（Phase3） | ⛔ PoC範囲外 |

---

## 12. 未確定事項（最新化）

| 項目 | 状況 | 対応方針 |
|---|---|---|
| 類似工事データ（Tool3） | **PoC範囲外**（§0-1） | 今回は扱わない |
| reactor_typeの絞り込み | **対応済み** | F3スキーマに炉型列追加＋struct_data＋後段フィルタで有効化（§0-3） |
| ゴールド指摘（NuRO正解）の確定 | 未確定 | 仮説 `gold_expectations.yaml` ＋ NuROアノテーションで段階的に確定（`RAG_VERIFICATION.md §A-4`） |
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

---

## 14. Phase 3 詳細要件：マルチモーダル補足資料 RAG

> **【PoC範囲外】**（§0-1）。本節（Tool4＝補足資料の写真/図面マルチモーダル）は**今回スコープ外**。
> 将来Phaseで扱う場合の参考要件として残置。現行の進捗・採否には含めない。

最終更新：2026-05-21

### 14-1. 目的

Phase 2 までの `load_supplement()` は Excel のテキスト部分しか読めていない。
Phase 3 では写真・図面（画像）をキャプション化して Vertex AI Search に投入し、
レビュー時に画像の内容も根拠として使えるようにする。

### 14-2. 対象ファイル形式と内容

| ファイル形式 | 内容 | 格納場所 |
|---|---|---|
| Excel (.xlsx) | 1シート1工事。「撤去前→撤去後」の写真2枚＋工事名テキスト | `data/knowledge/supplement/*.xlsx` |
| PPTX (.pptx) | 建屋平面図に工事進捗を色で重ね書き。工事ID・工事名・作業エリア名のテキストと位置・色情報 | `data/knowledge/supplement/*.pptx` |

### 14-3. 全体フロー

```
【前処理：データ投入時・一度だけ実行】

data/knowledge/supplement/
  ├── *.xlsx  ─── openpyxl で画像バイト列 + 周辺セルテキスト抽出
  └── *.pptx  ─── python-pptx で画像バイト列 + 近傍テキスト抽出
                          │
                          ▼
              Gemini 3 Flash（マルチモーダル）
              「{context_text} と書かれた枠の写真です。
                工事状態を説明してください。」
                          │
                          ▼ キャプション生成
              data/knowledge/supplement_captions/*.json  ← 確認・修正用の中間ファイル
                          │
                          ▼ 投入
              Vertex AI Search（nuro-supplement-knowledge）

【レビュー実行時：毎回】

knowledge_loader.load_supplement()
  → Vertex AI Search でキャプションをハイブリッド検索
  → 上位 N 件のキャプション + メタデータを返す
  → synthesis_node が Gemini レビュー生成プロンプトに組み込む
```

### 14-4. 新規追加スクリプト

#### `scripts/generate_supplement_captions.py`（新規）

```
役割  : 補足資料ファイルから画像を抽出し Gemini 3 でキャプションを生成する
入力  : data/knowledge/supplement/*.xlsx / *.pptx
出力  : data/knowledge/supplement_captions/{source_file}.json
実行  : uv run python scripts/generate_supplement_captions.py
       uv run python scripts/generate_supplement_captions.py --file 東北電力_補足.xlsx

出力JSONの1件の構造:
  {
    "id":               "{utility_name}_{source_file}_{image_index:03d}",
    "caption":          "PPパネルが完全に撤去されており工事完了状態",
    "utility_name":     "東北電力",
    "fee_type":         "解体費",
    "source_file":      "東北電力_補足.xlsx",
    "construction_name":"○○建屋解体工事",
    "context_text":     "撤去後",
    "original_format":  "excel"
  }
```

キャプション生成プロンプト（形式別）:

| 形式 | プロンプト |
|---|---|
| Excel | `「{construction_name}」の補足資料です。「{context_text}」と書かれた枠の写真です。工事の状態や作業内容を具体的に説明してください。` |
| PPTX | `建屋平面図の工事進捗図です。「{construction_name}」（{work_area}エリア）を示しています。図中の色分けや工事の進捗状態を説明してください。` |

使用モデル: `gemini-3-flash-preview`（マルチモーダル・1M トークン対応）

#### `scripts/create_datastores.py`（追記）

`nuro-supplement-knowledge` データストアを追加する。

```python
# 追加するエントリ
{
    "datastore_id": "nuro-supplement-knowledge",
    "display_name": "NuRO Supplement Knowledge (補足資料キャプション)",
    "env_key": "VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID",
}
```

#### `scripts/ingest_knowledge.py`（追記）

`--target supplement` オプションを追加し、生成済みキャプション JSON を Vertex AI Search に投入する。

```
ドキュメント構造:
  id          : "{utility_name}_{source_file}_{image_index:03d}"
  content     : キャプションテキスト（BM25 + ベクトル検索の対象）
  struct_data :
    knowledge_type    : "supplement"
    utility_name      : str
    fee_type          : str
    source_file       : str
    construction_name : str
    context_text      : str   （「撤去後」「撤去前」等）
    original_format   : "excel" or "pptx"
```

### 14-5. 変更するファイル

| ファイル | 変更内容 | I/F 変更 |
|---|---|---|
| `pyproject.toml` | `python-pptx` を依存追加 | — |
| `apps/backend/app/core/settings.py` | `VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID` / `_ENGINE_ID` を追加 | なし |
| `apps/backend/app/agents/reviewer/knowledge_loader.py` | `load_supplement()` 内部を Vertex AI Search 化 | **なし**（引数・戻り値は不変） |
| `scripts/create_datastores.py` | supplement データストアのエントリを追加 | — |
| `scripts/ingest_knowledge.py` | `--target supplement` オプションを追加 | — |
| `scripts/generate_supplement_captions.py` | **新規作成** | — |
| `apps/backend/tests/test_review_e2e.py` | supplement モックを Vertex AI Search レスポンス形式に更新 | — |

変更不要なファイル: `reviewer_agent.py`・`adk/agents.py`（`supplement_node()`）・API エンドポイント・フロントエンド

### 14-6. 環境変数の追加

`.env` に以下を追加する（`create_datastores.py` 実行後に出力された値を設定）:

```
VERTEX_SEARCH_SUPPLEMENT_DATASTORE_ID=nuro-supplement-knowledge
VERTEX_SEARCH_SUPPLEMENT_ENGINE_ID=nuro-supplement-engine
```

### 14-7. 精度評価と第2選択肢

Phase 3 完了後、NuRO 担当者によるレビュー精度評価を行い、以下の基準で第2選択肢への移行を判断する。

| 評価基準 | 第2選択肢への移行判断 |
|---|---|
| キャプション検索でヒットしない関連補足資料がある | Vertex AI Multimodal Embeddings の採用を検討 |
| キャプションの品質が低い（誤解釈が多い） | プロンプト改善 or モデルアップグレードを先に試みる |
| テキストと画像の混合検索が必要 | キャプション + Multimodal Embeddings のハイブリッドアプローチ |

**Document AI は使用しない**（写真の内容・図面の色情報を理解できないため）。Gemini 3 マルチモーダルで代替する。

### 14-8. 実装ステップ

| Step | 内容 |
|---|---|
| 1 | 補足資料ファイル収集・`data/knowledge/supplement/` に配置 |
| 2 | `python-pptx` 依存追加（`pyproject.toml`） |
| 3 | `create_datastores.py` に supplement データストアを追加して実行 |
| 4 | `generate_supplement_captions.py` を実装・実行（中間JSONで内容確認） |
| 5 | `ingest_knowledge.py` に `--target supplement` を追加して実行 |
| 6 | `knowledge_loader.load_supplement()` 内部を Vertex AI Search 化 |
| 7 | E2E テスト更新・全テスト通過確認 |
