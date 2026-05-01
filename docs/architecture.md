# アーキテクチャ

## 全体フロー

```
[委託会社資料]
 .xlsx / .docx
      │
      ▼
┌─────────────────────────────────────┐
│        data_extractor_agent         │
│                                     │
│  Layer 1: parser    （決定論的）     │
│  ファイル → 構造化テキスト            │
│  python-docx / openpyxl 使用   　　　│
│           │                         │
│           ▼                         │
│  Layer 2: mapper    （LLM 使用）     │
│  テキスト → スキーマ JSON         　　│
│  Gemini 2.5 Flash 使用          　　 │
│           │                         │
│           ▼                         │
│  Layer 3: validator （決定論的）     │
│  型変換・必須チェック・信頼度付与      │
└──────────────┬──────────────────────┘
               │ JSON + 信頼度メタデータ
               ▼
┌───────────────────────────────────────┐
│        cell_locator_agent             │
│  JSON キー → Excel セル番地 にマッピング│
│  YAML 定義（正確）+ Gemini（補完）      │
└──────────────┬────────────────────────┘
               │ {フィールド名: [セル番地]}
               ▼
┌─────────────────────────────────────┐
│     form_generation_pipeline        │
│  Excel テンプレートに値を書き込む  　　│
│  通常フィールド + 表形式セクション     │
└──────────────┬──────────────────────┘
               │
               ▼
      [転記済み Excel 完成]
```

---

## 設計思想：定義駆動（Definition-Driven）

本システムの核心は「**LLM を決定論的処理で挟む**」設計にある。

```
[決定論的]  parser    ファイル構造を確実に取り出す
    ↓
[LLM]       mapper    表記揺れを意味で吸収する
    ↓
[決定論的]  validator 型・必須・信頼度を機械的に検証する
```

LLM（Gemini）を使うのは mapper 層のみに限定する。  
LLM が暴走しても影響範囲が mapper 層に閉じるため、  
前後の決定論的処理で品質を保証できる。

---

## YAML 駆動の様式定義

様式ごとの定義を `frames/{frame_name}/{sheet_name}.yaml` で管理する。

```yaml
# frames/frameB/MRC1.yaml（抜粋）
extraction_schema:
  工事件名:
    type: string
    required: true
    synonyms: [工事件名, 工事名称, 件名, 業務名]  # 表記揺れパターン

  総額:
    type: number
    unit: 千円
    synonyms: [総額, 契約金額, 請負金額, 合計金額]
```

**YAML に追記するだけで対応範囲が広がる**。  
コードを変更せず、業務担当者が直接メンテできる設計。

---

## キャッシュ機構

cell_locator_agent の Gemini 呼び出し結果は  
`data/form_generation/cache/mapping_cache_{sheet_name}.json` にキャッシュされる。

Excel テンプレートのハッシュ値が一致する限りキャッシュを使い、  
Gemini を呼ばずにマッピングを再利用する。テンプレート変更時は自動で再判定。

---

## 信頼度スコア

data_extractor_agent は抽出した各フィールドに信頼度スコアを付与する。

| スコア | 意味 |
|---|---|
| 0.9〜1.0 | 完全一致または synonym 完全一致 |
| 0.7〜0.9 | synonym 一致だが値の解釈に若干の曖昧さあり |
| 0.5〜0.7 | synonym 外の表記から意味推測 |
| 0.0〜0.5 | 該当情報なし、または非常に曖昧 |

低信頼フィールドを UI でハイライトすることで、  
担当者が確認すべき箇所を限定できる（将来 UI 実装時に活用）。

---

## 将来構想：ADK マルチエージェント化

現在はスクリプトで逐次実行しているが、  
Google Agent Development Kit（ADK）を用いてエージェント群を協調動作させる構成に移行予定。

```
[将来構想]

OrchestratorAgent
    ├── data_extractor_agent  資料 → JSON
    ├── cell_locator_agent    JSON → セル番地
    ├── form_generator_agent  Excel 転記
    └── review_agent          転記結果の事前レビュー
```

各エージェントを ADK の AgentTool として登録することで、  
チャット UI からの自然言語指示で一連の処理を実行できるようになる。
