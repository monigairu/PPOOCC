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



一番の肝：決定論と確率論の分離

今回、数値はすべてPythonと数式から出していて、僕が頭で掛け算した値は1つもありません。LLMがやったのは「表を読む」「“300A超400A以下”を400A行に対応づける」「細断行が無いことに気づく」「台とt の不整合に気づく」「計算例と内訳の0.52t/0.63t矛盾に気づく」——つまり抽出・分類・判断・異常検知だけ。これがともきさんの一番大事にしている原則そのもので、転記システムでもこの線引きを死守すれば崩れません。

	•	Gemini（mapper）：抽出・行マッチング・分類・確信度付け
	•	決定論Python（validator/計算/cell_locator）：工数＝基準工数×数量×区域補正×効率補正、単位変換、SUM、セル書き込み

推奨フロー（1分類チャンクごと）

	1.	オーケストレータが次の分類を選ぶ（ADKのSequentialAgent）
	2.	検索/収集：その分類の基準工数表（基準工数シート）＋数量・区域（内訳明細書）＋費用（参考見積PDF）を取得 ← ここがVertex AI Searchの出番
	3.	抽出（Gemini）：基準工数・数量・区域・単位(台/t)をJSON化、確信度付き
	4.	検証（決定論）：必須項目・単位・レンジ確認。欠落/低確信/矛盾は未確定キューへ
	5.	HITL（あなたの役）：キューをまとめて質問→回答を確定→承認済みQ&Aをfew-shot/RAGに還元
	6.	計算（決定論）：工数算出と費用合算。YAMLマッピング＋cell_locatorでMRC1に書き込み
	7.	チャンク単位でレビュー→承認→次の分類

費用（参考見積PDF）は別経路で並走させる

工数経路（基準工数シート＋内訳）と、**費用経路（参考見積PDF→分類別に金額抽出→円で統一して合算）**は別パイプライン（ParallelAgent）にして、最後にMRC1上で突合します。今回僕が「サポートの効率補正」「弁の台/t」「電線管の基準無し」を指摘したように、2経路の整合チェックを最終バリデーションに入れるのがダミーデータQAでも本番でも効きます。金額は途中変換せず円で持ち、千円化は書き込み時だけ——という方針もそのまま活きます。

「君のように」インタラクティブにやる仕組み

自動パイプラインは無限に止まれないので、**「アシストモード（対話）」と「バッチモード（未確定キューに溜めて後でまとめて確認）」**を切り替え式にするのがおすすめです。PoCでは対話モード中心で、未確定だけを人に投げ、確定したらfew-shot/承認ケースとして蓄積（ともきさんのフィードバック活用計画と同じ）。これで2回目以降は「電線管はケーブル基準を流用」のような判断が自動化されていきます。

—

まずMRC1の1分類（例：配管）で、この「抽出→検証→未確定キュー→計算→書き込み」を細く通すのが最短だと思います。電線管の扱いは未確定のままなので、そこは早めに潰しておくと良い未確定キューの最初の1件になりますね。どこから具体化しますか？例えばこの工数計算ロジックを、転記システムのvalidator/計算モジュールに組み込める形（決定論Python関数＋YAML基準工数テーブル）に起こすこともできます。