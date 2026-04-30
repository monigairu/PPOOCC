# 実行手順

## 前提条件

- Python 3.12 以上
- [uv](https://docs.astral.sh/uv/) インストール済み
- Google Cloud プロジェクトへのアクセス権（Vertex AI 有効化済み）

---

## 環境構築

```bash
# 1. リポジトリをクローン
git clone https://git.dnode.co.jp/dnode/nuro_agentic_ai_step4.git
cd nuro_agentic_ai_step4

# 2. 依存パッケージをインストール
uv sync

# 3. 環境変数を設定
cp .env.example .env
# .env を編集して以下を設定:
#   GOOGLE_CLOUD_PROJECT=your-project-id
#   GOOGLE_CLOUD_LOCATION=us-central1（任意）

# 4. Google Cloud 認証
gcloud auth application-default login
```

---

## 実行パターン

### パターン1：委託会社資料 → 様式転記まで一気通貫（推奨）

```bash
PYTHONPATH=. uv run python scripts/run_data_extraction.py \
  --input data/source/会社A_工事概要報告書.xlsx \
  --sheet MRC1 \
  --frame frameB \
  --run-pipeline
```

出力ファイル：
- `data/extracted/会社A_工事概要報告書.json`（抽出結果 + 信頼度メタデータ）
- `data/extracted/会社A_工事概要報告書_data_only.json`（転記用 JSON）
- `data/form_generation/output/result_MRC1_extracted.xlsx`（転記済み様式）

### パターン2：抽出のみ（JSON を確認してから転記したい場合）

```bash
# Step 1: 抽出
PYTHONPATH=. uv run python scripts/run_data_extraction.py \
  --input data/source/会社A_工事概要報告書.xlsx \
  --sheet MRC1 --frame frameB

# 抽出結果を確認
cat data/extracted/会社A_工事概要報告書_data_only.json

# Step 2: 転記
PYTHONPATH=. uv run python scripts/run_form_generation.py \
  --input data/extracted/会社A_工事概要報告書_data_only.json \
  --sheet MRC1 --frame frameB
```

### パターン3：JSON から転記のみ（既存の JSON を使う場合）

```bash
PYTHONPATH=. uv run python scripts/run_form_generation.py \
  --input data/form_generation/input/sample_source.json \
  --sheet MRC1 --frame frameB
```

---

## コマンドライン引数

### run_data_extraction.py

| 引数 | 説明 | デフォルト |
|---|---|---|
| `--input` | 委託会社資料のパス（必須） | - |
| `--sheet` | 転記先シート名 | MRC1 |
| `--frame` | 様式名 | frameB |
| `--output` | 抽出結果 JSON の保存先 | data/extracted/{入力ファイル名}.json |
| `--run-pipeline` | 抽出後に転記パイプラインも実行 | False |

### run_form_generation.py

| 引数 | 説明 | デフォルト |
|---|---|---|
| `--input` | 入力 JSON ファイルのパス | data/form_generation/input/sample_source.json |
| `--sheet` | 処理対象のシート名 | MRC1 |
| `--frame` | 様式名 | frameB |

---

## テスト用ダミー資料

`data/source/` にテスト用の委託会社資料を用意している。

| ファイル | 形式 | 特徴 |
|---|---|---|
| 会社A_工事概要報告書.xlsx | Excel | 縦型テーブル。項目名に表記揺れあり（工事名称→工事件名 等） |
| 会社B_廃炉工事報告書.docx | Word | 箇条書き＋フリーテキスト。値に表記揺れあり（加圧水型→PWR、号機→号炉 等） |

---

## キャッシュのクリア

cell_locator のマッピング結果はキャッシュされる。  
テンプレートを変更した場合や再判定させたい場合は削除する。

```bash
rm data/form_generation/cache/mapping_cache_MRC1.json
```

---

## 新しい様式を追加する場合

1. `frames/frameB/` に `{sheet_name}.yaml` を作成
2. `extraction_schema`（抽出定義）と `sections`（セル定義）を記述
3. `data/form_generation/input/templates/` にテンプレート Excel を配置
4. `--sheet {sheet_name}` を指定して実行

コードの変更は不要。YAML の追加だけで対応できる。
