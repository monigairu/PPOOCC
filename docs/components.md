# コンポーネント一覧

## ディレクトリ構造

```
nuro-ai-platform/
├── apps/backend/app/
│   ├── agents/
│   │   ├── data_extractor/       # 委託会社資料 → JSON 変換
│   │   │   ├── data_extractor_agent.py
│   │   │   ├── parser.py
│   │   │   ├── mapper.py
│   │   │   ├── validator.py
│   │   │   └── SKILL.md
│   │   └── cell_locator/         # JSON → セル番地マッピング
│   │       ├── cell_locator_agent.py
│   │       └── SKILL.md
│   ├── core/
│   │   ├── ai_client.py          # Vertex AI（Gemini）共通クライアント
│   │   ├── cache_manager.py      # マッピングキャッシュ管理
│   │   ├── cell_writer.py        # Excel セルへの書き込み
│   │   ├── excel_io.py           # Excel ファイルの読み書き
│   │   ├── excel_scanner.py      # Excel ラベルセルのスキャン
│   │   ├── frame_config_loader.py # 様式定義 YAML の読み込み
│   │   └── skill_loader.py       # SKILL.md の読み込み・変数展開
│   ├── pipelines/
│   │   └── form_generation_pipeline.py  # 様式自動作成メインフロー
│   └── section_handlers/
│       └── tabular_handler.py    # 表形式セクションの書き込み処理
├── data/
│   ├── source/                   # 委託会社から受け取る資料（入力）
│   ├── extracted/                # 抽出済み JSON（data_extractor の出力）
│   └── form_generation/
│       ├── input/templates/      # 転記先 Excel テンプレート
│       ├── output/               # 転記済み Excel（最終出力）
│       └── cache/                # cell_locator のマッピングキャッシュ
├── frames/
│   └── frameB/
│       └── MRC1.yaml             # 様式定義（extraction_schema + sections）
└── scripts/
    ├── run_data_extraction.py    # 資料抽出 → 転記の実行スクリプト
    └── run_form_generation.py   # JSON → 転記のみの実行スクリプト
```

---

## agents/data_extractor

委託会社から提出された資料を読み込み、NuRO 様式に必要な情報を JSON として返す。

### data_extractor_agent.py
3層を束ねるエントリーポイント。  
`extract_data(source_file, sheet_name, frame_name)` を呼ぶだけで結果が得られる。

```python
result = extract_data("data/source/会社A_工事概要報告書.xlsx", "MRC1")
# result["data"]         → sample_source.json と同形式の辞書
# result["_metadata"]    → フィールドごとの信頼度・マッチ情報
# result["_validation"]  → 抽出率・警告・エラーのサマリー
```

### parser.py（Layer 1 / 決定論的）
ファイルを構造化テキストに変換する。LLM 不使用。

- `.xlsx` → openpyxl でシート・行・セルを読み取り、行番号付きのテキストに変換
- `.docx` → python-docx で段落・表を読み取り、構造を保持したテキストに変換

### mapper.py（Layer 2 / LLM 使用）
構造化テキストと extraction_schema を Gemini に渡し、  
フィールドへの紐付けと値の抽出を行う。  
どの synonym で一致したかを `matched_synonym` として記録する。

### validator.py（Layer 3 / 決定論的）
mapper の出力を検証・補正する。LLM 不使用。

- 必須フィールドの欠損チェック
- 型の妥当性チェック（date / number / enum）
- 信頼度スコアの範囲正規化（0.0〜1.0）
- 抽出率・低信頼フィールドのサマリー生成

### SKILL.md
mapper が Gemini に渡すプロンプト定義。  
`{{extraction_schema}}` `{{source_content}}` をプレースホルダとして持ち、  
実行時に動的に埋め込まれる。

---

## agents/cell_locator

JSON のキーを Excel のセル番地にマッピングする。

### cell_locator_agent.py
`determine_cell_mapping(json_data, workbook, sheet_name)` を提供する。

1. YAML 定義からセル番地を取得（正確な情報として優先）
2. Excel スキャンで補助情報を取得
3. Gemini に判定を依頼
4. 結果を `{フィールド名: [セル番地リスト]}` 形式で返す

マッピング結果はキャッシュに保存され、次回以降は Gemini を呼ばない。

---

## core

### ai_client.py
Vertex AI（Gemini）への呼び出しを抽象化する共通クライアント。  
全エージェントがこのモジュールを通じて LLM を呼ぶ。  
将来的なモデル切り替えはここだけを変更すれば良い。

### frame_config_loader.py
`frames/{frame_name}/{sheet_name}.yaml` を読み込む。  
`extraction_schema`（data_extractor 用）と `sections`（cell_locator 用）の両方を提供する。

### skill_loader.py
SKILL.md を読み込み、`{{変数名}}` 形式のプレースホルダを  
実行時の値で置換したプロンプト文字列を返す。

---

## frames/frameB/MRC1.yaml

様式定義ファイル。以下の 2 セクションで構成される。

### extraction_schema
data_extractor_agent が参照する抽出定義。  
フィールドごとに型・必須フラグ・synonym リストを定義する。

### sections
cell_locator_agent が参照するセル定義。  
フィールド名とセル番地の対応を記述する。

**このファイルを編集するだけで、新しい表記揺れへの対応と  
セルマッピングの変更が可能。コードの変更は不要。**
