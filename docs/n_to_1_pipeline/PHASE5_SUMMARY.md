# N対1 転記パイプライン Phase 5 実装まとめ

最終更新：2026-05-23（Phase 5 着手前・設計確定版）

---

## Phase 5 でやること

N 対 1 パイプライン全体を API エンドポイントとして公開し、手動確認スクリプトを整備する。

| 作業 | 内容 |
|---|---|
| STEP 7-a | `POST /api/transcribe/mrc1`（複数ファイル受付・非同期ジョブ方式）を実装 |
| STEP 7-b | `GET /api/jobs/{job_id}`（ジョブ進捗確認）を実装 |
| STEP 8 | `scripts/check_n_to_1_pipeline.py`（手動確認スクリプト）を実装 |
| テスト | エンドポイントの結合テストを作成 |

---

## ファイル構成（Phase 5 完了後）

```
nuro-ai-platform/
└── apps/backend/
    └── app/
        └── api/
            └── routes/
                └── transcribe.py        ← 新規（/api/transcribe/mrc1・/api/jobs/{job_id}）

└── scripts/
    └── check_n_to_1_pipeline.py         ← 新規
```

---

## APIエンドポイント一覧

### POST /api/transcribe/mrc1 — N対1 転記ジョブの受け付け

リクエスト: `files`（UploadFile のリスト）、`sheet`（デフォルト: "MRC1"）、`frame`（デフォルト: "frameB"）  
レスポンス: `job_id`、`status: "accepted"`

**処理の流れ:**

1. `job_id`（UUID）を発行して `job_store` に登録
2. ファイルの中身を `bytes` として読み込む（BackgroundTask 移管前に）
3. `_run_transcription_pipeline` を BackgroundTasks に登録して即 `job_id` を返す

### GET /api/jobs/{job_id} — ジョブ進捗の確認

リクエスト: `job_id`（パスパラメータ）  
レスポンス: `status`、`progress`、`result`（完了時）、`error`（失敗時）

フロントエンドは 2 秒間隔でポーリングし、`status === "completed"` になったら結果を表示する。  
タイムアウトは 120 秒を推奨。

---

## ジョブの状態管理

```python
# PoC用のインメモリストア（本番は Redis 等に置き換え）
job_store: dict[str, dict] = {}

# 状態遷移
"running"   → progress: 0〜99（ファイル読み込みで 50 まで・マージ・書き込みで 100）
"completed" → result: { output_path, skipped_cells, conflicts, formula_results }
"failed"    → error: エラーメッセージ
```

> **PoC の制限**: in-memory のため再起動でジョブ消失・マルチワーカー不可。  
> Redis への移行を想定して `job_store` を薄いラッパ関数で包む設計が望ましい。

---

## _run_transcription_pipeline の設計

```python
def _run_transcription_pipeline(
    job_id: str,
    file_contents: list[tuple[str, bytes]],  # (filename, bytes)
    sheet: str,
    frame: str,
) -> None:
    """
    BackgroundTasks から呼ばれる同期関数。

    【重要】async def にすると call_gemini（同期）がイベントループを 30-60 秒占有する。
    sync def にして FastAPI にスレッドプール実行させること。
    """
```

### パイプライン内部の処理順

```
1. ファイルを bytes から読み込んで SourceDocument に変換
   job_store[job_id]["progress"] += 50 / N（N = ファイル数）

2. 各 SourceDocument から Gemini 抽出（map_to_schema_from_doc）
   → data + formula_specs

3. formula_executor で FormulaSpec を検証
   → needs_review=True のものを conflicts に追加

4. merge_extractions で N 件をマージ
   → merged + conflicts（競合フィールド）

5. generate_form_from_dict で MRC1 に書き込み
   → writable: false スキップ・単位変換・tabular 書き込み

6. job_store[job_id] を "completed" に更新
   → result: { output_path, skipped_cells, conflicts, formula_results }
```

---

## レスポンスの result 構造

```json
{
  "status": "completed",
  "progress": 100,
  "result": {
    "output_path": "output/MRC1_result.xlsx",
    "skipped_cells": ["総額", "全体支払い対象金額"],
    "conflicts": [
      {
        "field": "工事件名",
        "candidates": [
          {"value": "○○配管解体工事", "source": "見積書.pdf"},
          {"value": "○○配管撤去工事", "source": "物量データ.xlsx"}
        ]
      },
      {
        "type": "formula_inconsistency",
        "formula_name": "配管工数",
        "python_result": 5.0,
        "gemini_result": 4.17,
        "note": "Python=5.0000 vs Gemini=4.1700",
        "source_location": {"file": "物量データ.xlsx", "sheet": "配管", "row": 5}
      }
    ],
    "formula_results": [
      {
        "name": "配管工数",
        "consistent": false,
        "source_location": {"file": "物量データ.xlsx", "sheet": "配管", "row": 5}
      }
    ]
  }
}
```

---

## STEP 8: 手動確認スクリプト

### 使い方

```bash
PYTHONPATH=. uv run python scripts/check_n_to_1_pipeline.py \
  --files data/見積書.pdf data/物量データ.xlsx data/工程表.xlsx \
  --sheet MRC1 \
  --frame frameB \
  --output output/MRC1_result.xlsx
```

### 出力内容

```
=== N対1 転記パイプライン 手動確認 ===

【Reader】
  見積書.pdf       → source_type=pdf, document_kind=見積書, 2 ページ
  物量データ.xlsx  → source_type=excel, document_kind=物量データ, 3 シート
  工程表.xlsx      → source_type=excel, document_kind=工程表, 1 シート

【抽出結果】（ファイルごと）
  ✅ 工事件名      "○○配管解体工事"    (見積書.pdf ページ1)   信頼度: high
  ✅ 総額          143500000 円         (見積書.pdf ページ2)   信頼度: high
  ✅ 工期開始日    "2025年4月"          (工程表.xlsx 行3)      信頼度: high
  ⚠️  実施内容      抽出できませんでした (信頼度: low)

【計算仕様の検証】
  ✅ 配管工数:  Python=5.00 人日 vs Gemini=5.00 人日  → 一致
     抽出元: 物量データ.xlsx シート:配管 行5

【マージ結果】
  競合なし

【書き込み結果】
  skipped_cells: ["総額", "全体支払い対象金額"]  ← writable:false のため

出力: output/MRC1_result.xlsx
```

---

## データの流れ（システム全体・Phase 5 完了後）

```
【API 経由】
POST /api/transcribe/mrc1
  ↓ job_id を即返す
  ↓ BackgroundTasks（スレッドプール）で実行

  1. select_reader → SourceDocument（N 件）
  2. map_to_schema_from_doc → extracted_data + formula_specs（N 件）
  3. execute_formula → FormulaResult（各 FormulaSpec）
  4. merge_extractions → merged + conflicts
  5. generate_form_from_dict → MRC1.xlsx

GET /api/jobs/{job_id}
  ↓ フロントエンドが 2 秒ごとにポーリング
  ↓ status=completed になったら result を表示

【手動確認】
python scripts/check_n_to_1_pipeline.py
  ↓ 同じパイプラインを CLI で実行
  ↓ 各ステップの結果をコンソールに出力
```

---

## テスト方針

| テストファイル | 確認内容 |
|---|---|
| `test_transcribe_endpoint.py` | `POST /api/transcribe/mrc1` が job_id を返すか |
| `test_transcribe_endpoint.py` | `GET /api/jobs/{job_id}` が status を返すか（Gemini モック使用） |
| `test_transcribe_endpoint.py` | 不正なファイル形式（.csv 等）でエラーになるか |

---

## 未解決の TODO（Phase 5 完了後に確認）

| 項目 | 対応方針 |
|---|---|
| 歩掛計算シートを入力資料として受領できるか確認 | PoC で formula_executor に渡せる形式か確認 |
| `全体支払い対象金額`（G19, K19）が数式セルかどうか | 確認 → `writable` フラグに反映 |
| 総額の税込 / 税抜の扱い | 単位変換ルールに影響するため確認 |
| N:1 マージの優先順位 | 見積書 > 工程表 > 物量データ をステークホルダーと合意 |
| フロントエンドの conflicts 表示 UI | 競合フィールドをどう表示するか（既存 /review との共用可否） |

---

## 用語集

| 用語 | 説明 |
|---|---|
| job_store | ジョブの状態を管理する in-memory dict。PoC 用。本番は Redis に置き換える |
| job_id | 転記ジョブごとに発行される UUID。フロントエンドがポーリングに使う |
| BackgroundTasks | FastAPI の機能。レスポンスを返した後にバックグラウンドで処理を実行する |
| skipped_cells | writable: false のため書き込みをスキップしたフィールドのリスト |
| conflicts | 複数ソースで値が食い違ったフィールドと、計算検証で不一致だった FormulaSpec のリスト |
| formula_results | FormulaSpec ごとの検証結果サマリー（consistent / source_location） |
