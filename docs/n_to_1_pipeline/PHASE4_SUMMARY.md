# N対1 転記パイプライン Phase 4 実装まとめ

最終更新：2026-05-23（Phase 4 着手前・設計確定版）

---

## Phase 4 でやること

複数ファイルの抽出結果を 1 つにマージし、MRC1 に書き込む層を実装する。

| 作業 | 内容 |
|---|---|
| STEP 5 | `field_merger.py`（競合解決・N:1 マージ）を新規実装 |
| STEP 6-a | `form_generation_pipeline.py` に `writable: false` スキップを追加 |
| STEP 6-b | `form_generation_pipeline.py` に書き込み直前の単位変換を追加 |
| STEP 6-c | `tabular_handler.write_tabular_section` に `max_rows` 安全弁を追加 |
| STEP 6-d | `frames/frameB/MRC1.yaml` の tabular columns に `unit: 千円` を追加 |
| テスト | merger・writer のテストを作成 |

---

## ファイル構成（Phase 4 完了後の予定）

```
nuro-ai-platform/
├── frames/frameB/
│   └── MRC1.yaml                          ← tabular columns に unit: 千円 を追加
│
└── apps/backend/
    └── app/
        ├── merger/                          ← 新規ディレクトリ
        │   ├── __init__.py
        │   └── field_merger.py              ← 新規
        └── pipelines/
            └── form_generation_pipeline.py  ← 拡張（writable / 単位変換 / max_rows）
        └── section_handlers/
            └── tabular_handler.py           ← 拡張（max_rows / 費用列の単位変換）

apps/backend/tests/
    ├── merger/                              ← 新規ディレクトリ
    │   ├── __init__.py
    │   └── test_field_merger.py             ← 新規
    └── writers/                             ← 新規ディレクトリ
        ├── __init__.py
        └── test_mrc1_writer.py              ← 新規
```

---

## STEP 5: field_merger.py

### 役割

N 件の抽出結果（各ファイルの `data` + `_metadata`）を受け取り、  
1 つのマージ済み辞書と競合リストを返す。

```python
def merge_extractions(
    extractions: list[dict],
) -> tuple[dict, list[dict]]:
    """
    Returns:
        merged:    { フィールド名: { value, source_file, confidence }, ... }
        conflicts: [ { field, candidates: [...] }, ... ]
    """
```

### マージの優先順位（PoC 時点のハードコード）

```python
SOURCE_PRIORITY = {
    "見積書":    1,   # 最優先
    "工程表":    2,
    "物量データ": 3,
    "その他":    99,
}

FIELD_SOURCE_OVERRIDE = {
    "工期開始日": "工程表",   # このフィールドだけ工程表を優先
    "工期終了日": "工程表",
    "総額":       "見積書",
    "実施内容":   "見積書",
}
```

> PoC 後は YAML 外部化を推奨（会社・様式ごとに上書き設定できるようにする）。

### 競合の判定基準

同一フィールドで複数ソースの値が異なる場合に競合とする。  
優先ソースの値を `merged` に採用し、残りを `conflicts` に記録する。

### 解体機器リストのマージ（normalize_equipment_list）

```python
def normalize_equipment_list(raw_lists: list[list[dict]]) -> list[dict]:
    """
    PoC では単純結合のみ（重複が起きうるが許容）。
    重複候補は conflicts に積んで人間確認に回す。

    TODO(PoC後): Gemini を使った名寄せロジックを実装する。
    「配管（50A）」と「既設50A配管撤去」を同一機器として統合する。
    """
```

---

## STEP 6: form_generation_pipeline.py の拡張

### 追加1: writable: false のスキップ

```python
for field_name, field_def in yaml_config["extraction_schema"].items():
    if not field_def.get("writable", True):
        skipped_cells.append(field_name)
        continue
    ...
```

### 追加2: 書き込み直前の単位変換

```python
from apps.backend.app.core.unit_converter import convert_unit

target_unit = field_def.get("unit")
if target_unit and target_unit != "円":
    value = convert_unit(value, from_unit="円", to_unit=target_unit)
    if value is None:
        logger.warning(f"[unit_converter] {field_name} の変換失敗。スキップ。")
        continue
```

### 追加3: tabular_handler への max_rows 追加

```python
write_tabular_section(ws, merged_data, yaml_config, max_rows=200)
```

`max_rows` を超えた場合は先頭 200 行のみ書き込んで WARNING ログを出す。  
結果の `WriteResult.warnings` に含めてフロントエンドが表示できるようにする。

---

## MRC1.yaml tabular columns への unit 追加

解体機器表の費用列（計画_費用 / 実績_費用）も千円変換が必要。

```yaml
# frames/frameB/MRC1.yaml の columns への追加
columns:
  - {name: 計画_費用,  column: J, unit: 千円}   ← unit を追加
  - {name: 実績_費用,  column: N, unit: 千円}   ← unit を追加
  # unit が未定義の列は変換しない
```

`tabular_handler.write_tabular_section` がこの `unit` を読んで変換する。

---

## 単位変換のタイミング整理

| タイミング | 処理 | 単位 |
|---|---|---|
| Gemini 抽出時（mapper.py） | 値を抽出 | **円**（Gemini に指示） |
| マージ時（field_merger.py） | 競合解決・統合 | **円**（変換しない） |
| 計算時（formula_executor.py） | 再計算・検証 | **円** or result_unit 通り |
| 書き込み直前（form_generation_pipeline.py） | **円 → 千円** | **千円** |
| 解体機器表（tabular_handler.py） | **円 → 千円**（費用列のみ） | **千円** |

---

## テスト方針

| テストファイル | 確認内容 |
|---|---|
| `test_field_merger.py` | 優先順位通りにマージされるか・競合が conflicts に入るか・FIELD_SOURCE_OVERRIDE が効くか |
| `test_mrc1_writer.py` | `writable: false` のフィールドがスキップされるか・単位変換が正しく行われるか・max_rows 安全弁 |

---

## データの流れ（Phase 4 完了後）

```
N 件の抽出結果（data + _metadata + formula_specs）
    ↓
merge_extractions()
    ↓
merged: { フィールド名: { value（円）, source_file, confidence } }
conflicts: [ 競合フィールドのリスト ]
    ↓
execute_formula() で FormulaSpec を検証
    → needs_review=True のものを conflicts に追加
    ↓
generate_form_from_dict()（既存関数を拡張）
    ├── writable: false のフィールドをスキップ → skipped_cells に記録
    ├── unit_converter で円 → 千円 に変換
    └── write_to_cell で MRC1 に書き込み
    ↓
write_tabular_section()（拡張）
    ├── max_rows=200 の安全弁
    ├── 費用列は unit_converter で円 → 千円 に変換
    └── 解体機器表に書き込み
    ↓
MRC1.xlsx（出力）
```

---

## Phase 5 着手前の準備事項

```
① upload.py（既存エンドポイント）の構造確認
   複数ファイル対応に変える際の影響範囲を把握する
   既存の単一ファイル転記フロー（POST /api/upload）との共存方針を決める

② job_store の設計確認
   PoC では in-memory dict で可（再起動でジョブ消失・マルチワーカー不可は許容）
   Redis 移行を想定したインターフェースにしておくか確認

③ フロントエンド側のポーリング実装
   GET /api/jobs/{job_id} へのポーリング間隔（推奨: 2 秒）と
   タイムアウト設定（推奨: 120 秒）を事前に合意する
```
