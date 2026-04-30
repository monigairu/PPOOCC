# nuro-ai-platform

廃炉情報管理システム（NuRO）様式自動作成 PoC

電力会社の委託会社が提出した資料（Excel/Word）を読み込み、  
NuRO 提出様式（Excel）へ自動転記するシステム。

---

## システム概要

```
[委託会社資料 .xlsx/.docx]
        ↓
[data_extractor_agent]  構造化 JSON に変換
        ↓
[cell_locator_agent]    JSON → セル番地にマッピング
        ↓
[form_generation_pipeline]  Excel テンプレートに転記
        ↓
[NuRO 提出様式 .xlsx 完成]
```

電力会社担当者が資料を UI からアップロードし、AI が転記済み様式を返す。  
担当者は確認・修正後、NuRO に提出する。

---

## 前提条件

| 項目 | バージョン / 内容 |
|---|---|
| Python | 3.12 以上 |
| uv | 最新版（[インストール手順](https://docs.astral.sh/uv/getting-started/installation/)） |
| Google Cloud SDK | `gcloud` コマンドが使える状態 |
| GCP プロジェクト | Vertex AI API が有効化済み |

---

## クイックスタート

```bash
# 1. リポジトリをクローン
git clone https://git.dnode.co.jp/dnode/nuro_agentic_ai_step4.git
cd nuro-ai-platform

# 2. 依存パッケージをインストール
uv sync

# 3. 環境変数を設定
cp .env.example .env
# .env を編集して GOOGLE_CLOUD_PROJECT などを記入

# 4. Google Cloud 認証（初回のみ）
gcloud auth application-default login

# 5. 委託会社資料 → 様式転記まで一気通貫で実行
PYTHONPATH=. uv run python scripts/run_data_extraction.py \
  --input data/source/会社A_工事概要報告書.xlsx \
  --sheet MRC1 --frame frameB \
  --run-pipeline
```

出力: `data/form_generation/output/result_MRC1_extracted.xlsx`

---

## 使い方

### パターン1：委託会社資料 → 様式転記まで一気通貫（推奨）

```bash
PYTHONPATH=. uv run python scripts/run_data_extraction.py \
  --input data/source/会社A_工事概要報告書.xlsx \
  --sheet MRC1 --frame frameB \
  --run-pipeline
```

### パターン2：JSON から転記のみ

```bash
PYTHONPATH=. uv run python scripts/run_form_generation.py \
  --input data/form_generation/input/sample_source.json \
  --sheet MRC1 --frame frameB
```

### 新しい様式を追加する場合

`frames/<frame名>/<シート名>.yaml` を作成するだけでコード変更不要。  
詳細は [docs/how_to_run.md](docs/how_to_run.md) を参照。

---

## プロジェクト構成

```
nuro-ai-platform/
├── apps/backend/app/
│   ├── agents/
│   │   ├── data_extractor/   # 委託会社資料 → JSON 変換
│   │   └── cell_locator/     # JSON → セル番地マッピング
│   ├── core/                 # 共通モジュール（AI クライアント等）
│   ├── pipelines/            # 様式自動作成パイプライン
│   └── section_handlers/     # 表形式セクション処理
├── data/
│   ├── source/               # 委託会社から受け取る資料（入力）
│   ├── extracted/            # 抽出済み JSON（自動生成）
│   └── form_generation/      # テンプレート・出力 Excel
├── frames/frameB/            # 様式定義 YAML
├── scripts/                  # 実行スクリプト
└── docs/                     # 設計ドキュメント
```

---

## 開発

### テスト実行

```bash
uv run pytest
```

### 主要ドキュメント

| ドキュメント | 内容 |
|---|---|
| [docs/requirements.md](docs/requirements.md) | 要件・背景・PoCゴール・前任PoCとの差別化 |
| [docs/architecture.md](docs/architecture.md) | システム設計・3層アーキテクチャ・将来構想 |
| [docs/components.md](docs/components.md) | 各コンポーネントの責務 |
| [docs/how_to_run.md](docs/how_to_run.md) | 環境構築・実行手順・引数説明 |

---

## トラブルシューティング

### `uv sync` で SSL エラーが出る

社内ネットワークの SSL 検査が原因の可能性があります。  
`--native-tls` オプションを付けて再実行してください。

```bash
uv sync --native-tls
```

恒久対応として `~/.zshrc` に以下を追記すると毎回付けずに済みます。

```bash
export UV_NATIVE_TLS=true
```

### GCP 認証エラー

```bash
gcloud auth application-default login
```

を実行して再認証してください。  
それでも解決しない場合は `.env` の `GOOGLE_CLOUD_PROJECT` が正しいか確認してください。

### キャッシュが古くて結果に反映されない

```bash
rm data/form_generation/cache/mapping_cache_MRC1.json
```

テンプレート Excel を変更した場合も同様にキャッシュを削除してください。

### `No module named 'apps'` エラー

`PYTHONPATH=.` を付けて実行してください。

```bash
PYTHONPATH=. uv run python scripts/run_data_extraction.py ...
```

---

## ライセンス / 取り扱い

本リポジトリは社内利用専用です。外部公開しないでください。
