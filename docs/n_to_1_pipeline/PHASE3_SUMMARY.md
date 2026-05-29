# N対1 転記パイプライン Phase 3 実装まとめ

最終更新：2026-05-23（Phase 3 完了後の状態を反映）

---

## Phase 3 でやったこと

既存の `mapper.py` / `validator.py` を拡張し、`SourceDocument` を入力として構造化データと計算仕様（FormulaSpec）を抽出できるようにした。

| 作業 | 内容 |
|---|---|
| STEP 4-a | `ai_client.py` に `call_gemini_structured()` を追加（structured output 対応・既存 `call_gemini` は変更なし） |
| STEP 4-b | `mapper.py` に `map_to_schema_from_doc()` + 定数群を追加（既存 `map_to_schema` のシグネチャは変更なし） |
| STEP 4-c | `validator.py` の `validate_and_finalize()` に `formula_specs` 素通しを追加 |
| STEP 4-d | `mapper.py` に `infer_plan_actual()` を実装（キーワードマッチ → Gemini フォールバック） |
| テスト | `tests/extractors/test_gemini_extractor.py` を 18 件作成・全件パス |

---

## ファイル構成（Phase 3 完了後）

```
nuro-ai-platform/
└── apps/backend/
    └── app/
        ├── core/
        │   └── ai_client.py              ← call_gemini_structured() 追加
        └── agents/
            └── data_extractor/
                ├── mapper.py             ← map_to_schema_from_doc() / infer_plan_actual() / 定数群を追加
                └── validator.py          ← formula_specs 素通しを追加

apps/backend/tests/
    └── extractors/                       ← 新規ディレクトリ
        ├── __init__.py
        └── test_gemini_extractor.py      ← 新規（18件）
```

---

## 既存パイプラインとの関係

既存の 1 ファイル転記フロー（`map_to_schema` → `validate_and_finalize`）のシグネチャは**変更なし**。  
N対1 専用の新関数 `map_to_schema_from_doc` を追加する形にしたため、E2E テスト 33 件は全件パスを維持。

```
【既存フロー（変更前後で同じ）】
parse_file(file_path: str) → str
    ↓
map_to_schema(parsed_text: str, ...) → dict     ← シグネチャ変更なし
    ↓
validate_and_finalize(...) → dict

【N対1 フロー（新規追加）】
SourceDocument.text_content
    ↓
map_to_schema_from_doc(source_doc, ...) → dict  ← 新規関数
    ↓ call_gemini_structured（structured output）
    ↓
{
  extracted_data: { フィールド名: 値（円単位） },
  field_metadata: { フィールド名: { confidence, source_location } },
  formula_specs:  [ FormulaSpec, ... ],
}
    ↓
validate_and_finalize(...) → dict               ← formula_specs が素通りで追加される
{
  data:          { フィールド名: 値（円単位） },
  _metadata:     { confidence, source_location },
  _validation:   { extraction_rate, warnings, errors },
  formula_specs: [ FormulaSpec, ... ],          ← 追加
}
```

---

## ai_client.py に追加した関数

```python
def call_gemini_structured(
    prompt,
    response_schema: dict,          # JSON Schema 形式の dict
    model_name: str = "gemini-3.5-flash",
    system_instruction: str = "",
) -> dict:
    """
    structured output（response_mime_type="application/json"）を使って JSON dict を返す。
    既存の call_gemini とは独立。既存テストへの影響ゼロ。
    """
```

---

## mapper.py に追加した要素

### 定数

| 定数 | 内容 |
|---|---|
| `EXTRACTION_SYSTEM_PROMPT` | 金額を円単位で返す・null を推測しない などのルール |
| `DOCUMENT_KIND_HINTS` | document_kind ごとの Gemini へのヒント文 |
| `FORMULA_SPEC_PROMPT_ADDITION` | 計算仕様の抽出指示 |
| `PLANNING_KEYWORDS` | `["参考見積書", "申請", "予定", "計画"]` |
| `ACTUAL_KEYWORDS` | `["実績報告", "完了報告", "確定", "検収"]` |
| `_CONFIDENCE_MAP` | `"high"→0.9, "medium"→0.7, "low"→0.3` |
| `EXTRACTION_RESPONSE_SCHEMA` | `call_gemini_structured` に渡す JSON Schema |

### 新規関数

```python
def map_to_schema_from_doc(source_doc: SourceDocument, sheet_name: str, frame_name: str = "frameB") -> dict
```

- Gemini に渡す: EXTRACTION_SYSTEM_PROMPT + document_kind ヒント + extraction_schema YAML + text_content + FORMULA_SPEC_PROMPT_ADDITION
- Gemini が返す: `{ "extracted_fields": {フィールド名: {value, confidence, source_context}}, "formula_specs": [...] }`
- 変換して返す: `extracted_data` + `field_metadata` + `formula_specs: list[FormulaSpec]`
- スキーマ全フィールドの補完: Gemini が返さなかったフィールドは `null` + confidence `0.0` で補完

```python
def infer_plan_actual(source_doc: SourceDocument) -> str
```

- キーワードカウントで先に判定（PLANNING_KEYWORDS vs ACTUAL_KEYWORDS）
- 同数の場合は `call_gemini` に問い合わせ
- Gemini が例外 → `"不明"` を返す

---

## Gemini に返させる JSON 構造

```json
{
  "extracted_fields": {
    "工事件名": {
      "value": "○○配管解体工事",
      "confidence": "high",
      "source_context": "ページ1 工事件名欄"
    },
    "総額": {
      "value": 143500000,
      "confidence": "high",
      "source_context": "ページ2 御見積金額欄"
    }
  },
  "formula_specs": [
    {
      "formula_name": "配管工数",
      "expression": "ceil(weight * manhour_per_ton)",
      "variables": {"weight": 1.5, "manhour_per_ton": 2.78},
      "gemini_result": 5.0,
      "result_unit": "人日",
      "source_location": {"file": "物量データ.xlsx", "sheet": "配管", "row": 5}
    }
  ]
}
```

> **金額は円単位**: Gemini には「円単位のまま返す」と指示。千円変換は Phase 4 の書き込み直前のみ。

---

## validator.py の変更

`validate_and_finalize()` の戻り値に `formula_specs` の素通しを追加。  
型チェックは行わない（FormulaSpec のバリデーションは formula_executor が担当）。

```python
result = {
    "data": validated_data,
    "_metadata": validated_metadata,
    "_validation": validation_summary,
}
if "formula_specs" in mapper_result:
    result["formula_specs"] = mapper_result["formula_specs"]
return result
```

---

## テスト結果

Phase 3 完了時点でテスト全体 **128 件中 126 件パス**（既存 2 件の失敗は変わらず）。

| テストファイル | 件数 | 主な確認内容 |
|---|---|---|
| `test_gemini_extractor.py` | 18 | EXTRACTION_RESPONSE_SCHEMA 構造・円単位返却・confidence マッピング・FormulaSpec 型変換・malformed spec スキップ・infer_plan_actual キーワード判定・Gemini フォールバック・例外時は「不明」 |

---

## 実装上の決定事項

- **シグネチャを変えない**: 既存 `map_to_schema(parsed_text: str, ...)` は変更なし。新規 `map_to_schema_from_doc(source_doc, ...)` として追加。E2E テスト 33 件への影響ゼロ。
- **SourceDocument の型ヒントは文字列で受ける**: `mapper.py` から `readers/source_document.py` への直接インポートを避け、循環インポートリスクを回避。
- **FormulaSpec のインポートは関数内**: `map_to_schema_from_doc` 内で遅延インポートすることで、`tools` → `mapper` の依存は発生しない。
- **malformed FormulaSpec はスキップ**: 必須キー（formula_name, expression, variables, gemini_result, result_unit）が欠けた仕様は例外を出さずに無視する。

---

## データの流れ（Phase 3 完了後）

```
SourceDocument（N 件）
    ↓
map_to_schema_from_doc() ← mapper.py 拡張版
    ↓ call_gemini_structured（structured output）
    ↓
{
  extracted_data: { フィールド名: 値（円単位） },
  field_metadata: { フィールド名: { confidence, source_location } },
  formula_specs:  [ FormulaSpec, ... ],
}
    ↓
validate_and_finalize() ← validator.py（formula_specs 素通し）
    ↓
{
  data:          { フィールド名: 値（円単位） },
  _metadata:     { confidence, source_location },
  _validation:   { extraction_rate, warnings, errors },
  formula_specs: [ FormulaSpec, ... ],
}
    ↓
（Phase 4 へ）field_merger・form_generation_pipeline
```

---

## Phase 4 着手前の準備事項

```
① field_merger.py の設計確認
   複数 SourceDocument の抽出結果をリストで持ち回る形を確認

② SOURCE_PRIORITY の合意
   見積書 > 工程表 > 物量データ という優先順位をステークホルダーと合意してから実装

③ tabular_handler.py の費用列確認
   計画_費用 / 実績_費用 の列に unit: 千円 を追加するための
   tabular_handler.py の現状の引数・動作を確認する

④ form_generation_pipeline.py の確認
   generate_form_from_dict() に writable:false スキップと単位変換を追加するための
   現状の実装を確認する
```
