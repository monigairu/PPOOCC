"""
Layer 3: バリデーター

mapper の出力を検証し、以下の処理を行う:
  - 必須フィールドの欠損チェック
  - 型の妥当性チェック（date, number, enum 等）
  - none_values による「なし」正規化（対象号炉 等）
  - field_normalizations による表記統一（支払い対象可否 等）
  - 信頼度スコアの検証・補正
  - sample_source.json と同形式の最終 JSON を出力

LLM は一切使用しない（決定論的処理）。
"""
import re
from pathlib import Path

import yaml


def validate_and_finalize(
    mapper_result: dict,
    sheet_name: str,
    frame_name: str = "frameB",
) -> dict:
    """
    mapper の出力を検証・補正し、最終的な JSON を返す。

    Args:
        mapper_result: mapper が返した辞書
            {
                "extracted_data": {...},
                "field_metadata": {...}
            }
        sheet_name: 対象シート名
        frame_name: 様式名

    Returns:
        {
            "data": sample_source.json と同形式の辞書,
            "_metadata": 信頼度・マッチ情報を含む辞書,
            "_validation": 検証結果のサマリー
        }
    """
    schema = _load_extraction_schema(frame_name, sheet_name)
    extracted = mapper_result.get("extracted_data", {})
    metadata = mapper_result.get("field_metadata", {})

    validated_data: dict = {}
    validated_metadata: dict = {}
    warnings: list[str] = []
    errors: list[str] = []

    for field_name, field_def in schema.items():
        field_type = field_def.get("type", "string")
        required = field_def.get("required", False)

        # 抽出データから値を取得
        raw_value = extracted.get(field_name)
        field_meta = metadata.get(field_name, {})

        # --- 欠損チェック ---
        if raw_value is None or raw_value == "":
            if required:
                errors.append(f"必須フィールド '{field_name}' が未抽出です")
                field_meta["confidence"] = 0.0
            validated_data[field_name] = raw_value
            validated_metadata[field_name] = field_meta
            continue

        # --- 型別バリデーション ---
        if field_type == "enum":
            allowed = field_def.get("values", [])
            validated_value, type_warning = _validate_enum(
                field_name, raw_value, allowed
            )
        elif field_type == "number":
            validated_value, type_warning = _validate_number(
                field_name, raw_value
            )
        elif field_type == "date":
            validated_value, type_warning = _validate_date(
                field_name, raw_value
            )
        elif field_type == "list":
            validated_value, type_warning = _validate_list(
                field_name, raw_value, field_def
            )
        else:
            # string / text: none_values による正規化を適用
            validated_value, type_warning = _validate_string(
                field_name, raw_value, field_def
            )

        if type_warning:
            warnings.append(type_warning)

        validated_data[field_name] = validated_value
        validated_metadata[field_name] = field_meta

    # --- 信頼度の補正 ---
    for field_name, meta in validated_metadata.items():
        confidence = meta.get("confidence", 0.5)

        # 必須フィールドが null → 信頼度を 0 に
        if validated_data.get(field_name) is None:
            schema_def = schema.get(field_name, {})
            if schema_def.get("required", False):
                meta["confidence"] = 0.0
                continue

        # 信頼度の範囲を 0.0 ~ 1.0 に正規化
        if isinstance(confidence, (int, float)):
            meta["confidence"] = max(0.0, min(1.0, float(confidence)))

    # --- サマリー作成 ---
    total = len(schema)
    extracted_count = sum(
        1 for v in validated_data.values()
        if v is not None and v != ""
    )
    high_confidence = sum(
        1
        for m in validated_metadata.values()
        if m.get("confidence", 0) >= 0.7
    )
    low_confidence_fields = [
        name
        for name, m in validated_metadata.items()
        if 0 < m.get("confidence", 0) < 0.7
    ]

    validation_summary = {
        "total_fields": total,
        "extracted_fields": extracted_count,
        "high_confidence_fields": high_confidence,
        "low_confidence_fields": low_confidence_fields,
        "warnings": warnings,
        "errors": errors,
        "extraction_rate": f"{extracted_count}/{total} ({extracted_count/total*100:.0f}%)",
    }

    result = {
        "data": validated_data,
        "_metadata": validated_metadata,
        "_validation": validation_summary,
    }
    # formula_specs は型チェックなしで素通し（Phase 3 拡張）
    if "formula_specs" in mapper_result:
        result["formula_specs"] = mapper_result["formula_specs"]
    return result


def _validate_string(
    field_name: str, value, field_def: dict
) -> tuple[str, str | None]:
    """
    string / text 型のバリデーション。

    none_values が定義されている場合、該当する値を「なし」に正規化する。
    例: 「該当なし」「N/A」「-」→「なし」
    """
    str_value = str(value).strip()
    none_values = field_def.get("none_values", [])

    if none_values and str_value in none_values:
        if str_value != "なし":
            return "なし", (
                f"'{field_name}': '{str_value}' を 'なし' に正規化しました"
            )

    return str_value, None


def _validate_enum(
    field_name: str, value, allowed: list[str]
) -> tuple[str, str | None]:
    """enum 型のバリデーション。"""
    str_value = str(value).strip()
    if str_value in allowed:
        return str_value, None
    # 部分一致を試みる
    for candidate in allowed:
        if candidate in str_value or str_value in candidate:
            return candidate, (
                f"'{field_name}': '{str_value}' を '{candidate}' に補正しました"
            )
    return str_value, (
        f"'{field_name}': '{str_value}' は許可値 {allowed} に含まれません"
    )


def _validate_number(
    field_name: str, value
) -> tuple[str, str | None]:
    """number 型のバリデーション。カンマ区切りの数値を処理。"""
    str_value = str(value).strip()

    # カンマ・円記号・千円等を除去して数値として解釈可能か確認
    cleaned = re.sub(r"[,，円千万億\s]", "", str_value)

    try:
        float(cleaned)
        # 元の形式を保持（カンマ付き等）。正規化は後段で行う
        return str_value, None
    except ValueError:
        return str_value, (
            f"'{field_name}': '{str_value}' を数値として解釈できません"
        )


def _validate_date(
    field_name: str, value
) -> tuple[str, str | None]:
    """date 型のバリデーション。日付形式の妥当性を簡易チェック。"""
    str_value = str(value).strip()

    # 一般的な日付パターンを許容
    date_patterns = [
        r"\d{4}年\d{1,2}月",       # 2024年10月
        r"\d{4}/\d{1,2}",          # 2024/10
        r"\d{4}-\d{1,2}",          # 2024-10
        r"令和\d{1,2}年\d{1,2}月",  # 令和6年10月
        r"R\d{1,2}\.\d{1,2}",      # R6.10
    ]

    for pattern in date_patterns:
        if re.search(pattern, str_value):
            return str_value, None

    return str_value, (
        f"'{field_name}': '{str_value}' を日付として認識できません"
    )


def _validate_list(
    field_name: str, value, field_def: dict
) -> tuple[list | str, str | None]:
    """
    list 型のバリデーション。

    field_normalizations が定義されている場合、
    表内の各行・各フィールドの値を正規化する。
    例: 支払い対象可否「対象」→「支払い対象」、「対象外」→「支払い対象外」
    """
    if not isinstance(value, list):
        return value, (
            f"'{field_name}': list型が期待されていますが {type(value).__name__} です"
        )

    normalizations = field_def.get("field_normalizations", {})
    if not normalizations:
        return value, None

    normalized_list = []
    for row in value:
        if not isinstance(row, dict):
            normalized_list.append(row)
            continue

        normalized_row = dict(row)
        for col_name, rules in normalizations.items():
            if col_name not in normalized_row:
                continue
            cell_value = str(normalized_row[col_name]).strip()

            # 正方向の正規化（例: 「対象」→「支払い対象」）
            if cell_value in rules.get("synonyms", []):
                normalized_row[col_name] = rules["canonical"]
            # 負方向の正規化（例: 「対象外」→「支払い対象外」）
            elif cell_value in rules.get("synonyms_negative", []):
                normalized_row[col_name] = rules["canonical_negative"]

        normalized_list.append(normalized_row)

    return normalized_list, None


def _load_extraction_schema(frame_name: str, sheet_name: str) -> dict:
    """YAML ファイルから extraction_schema を読み込む。"""
    yaml_path = Path("frames") / frame_name / f"{sheet_name}.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(
            f"様式定義ファイルが見つかりません: {yaml_path}"
        )

    with open(yaml_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    schema = config.get("extraction_schema")
    if schema is None:
        raise ValueError(
            f"extraction_schema が定義されていません: {yaml_path}"
        )

    return schema