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
| [docs/architecture.md](docs/architecture.md) | システム設計（様式自動作成・転記系） |
| [docs/preliminary_review/ARCHITECTURE.md](docs/preliminary_review/ARCHITECTURE.md) | システム設計（事前レビュー・RAG） |
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



【目的】
実装の前に認識合わせをしたい。次の3点について、ソース資料(data/source/dev_inputs)と
既存コード(mapper.py / formula_executor.py / manhour_lookup.py 等)から読み取れる事実を整理し、
「確定事項」と「未確定事項」を文書化してください。実装・コード変更はまだしない。
※労務費の按分は今回のスコープ外。扱わないこと。

【守る原則】
- 決定論と確率論の分離：四則演算・基準工数引き・単位変換・集計・セル書き込みは決定論Python。
  LLMは抽出・分類・行マッチング・根拠提示のみ。数値をLLMに計算させない。
- 既存E2E(33本)を壊さない。今回は調査・文書化のみ。
- 数値は必ずソースの実値を使う。推測で埋めない。不明はそのまま未確定として列挙。

【① 解体機器名称の粒度（要調査・要確認）】
- 物量データ(機器名称/口径/重量/機器分類/作業区域)で、機器がどの粒度で記載されているか
  実データで確認（例：配管500A単位か、機器分類=配管単位か）。
- MRC1の出力粒度（分類/機器単位に集計し、口径別は合算）との対応関係(N対1)を表で整理。
- 「機器名称→MRC1分類バケツ」のマッピング表を作る。電線管→配管(電線管含む)は確定。
  それ以外で曖昧なものは未確定に回す。

【② 工数算出（下記ルールは人間側で確定済み。決定論ロジックの仕様とする）】
工数(人工=人日) = 基準工数 × 数量 × 区域補正 × 作業効率補正
- 基準工数：歩掛Excelの該当シート(配管/弁/サポート/ケーブル基準工数)から、分類×作業×口径で引く。
- 作業：機器取外し・細断・収納・移動保管。
  弁は「機器取外し=駆動部取外し容器収納＋本体取外し容器収納」の合算＋移動保管。
- 単位：弁=人工/台(数量=台)、他=人工/t(数量=t)。
- 区域補正：A=1.12 / B=1.30 / C,D=1.55 / マスク=1.90。作業区域は物量データから取る。
- 作業効率補正：配管の機器取外しのみ狭所1.35、他は1.00。
- 細断：配管のみ、32A以下は対象外。今回データは内訳上500Aのみ細断対象。
- 移動・保管：基準工数1.04固定。
- 集計：機器ごとに全作業・全口径を合算してMRC1の工数列へ。
- 【確認事項】MRC1の工数列は「人・時間」、当方の基準工数は「人工＝人日」。
  人日→人・時間の換算（保全区域6.5時間/日を一度だけ掛けるか等）をどこで1回行うか明示。不明なら未確定に。
- 【重要】工数式が資料に無い分類(ポンプ・モータ、その他機器、電線管の取外し/収納など)は
  勝手に数式を作らない。未確定として列挙し、本番で実現性が通る代替案
  (既存歩掛の流用 / 見積から逆算 等)の選択肢だけ提示する。

【③ 費用算出（要調査・要確認。按分はしない）】
- MRC1の費用列がどう作られるべきか、見積PDFと既存コードから読み取れる方法を整理。
- 按分は行わない。工数→労務費の関係(工数×単価か否か)が必要かもソースから判断し、不明なら未確定に。

【成果物】
- 「認識合わせドキュメント」をMarkdownで。confirmed（確定ルール・マッピング）と
  open questions（未確定キューの初期項目）に分けて出力。
- open questionsには最低限：機器名称粒度のマッピング曖昧分／人日→人・時間換算／
  工数式が無い分類の扱い／費用算出方法、を含める。
- 実装・コード変更はしない。文書化と質問の整理のみ。
