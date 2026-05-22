"""
セル番地特定エージェント
"""
import json
import re
from pathlib import Path

from openpyxl.workbook.workbook import Workbook

from apps.backend.app.core.ai_client import call_gemini
from apps.backend.app.core.excel_scanner import scan_label_cells
from apps.backend.app.core.frame_config_loader import load_frame_config, extract_cell_definitions
from apps.backend.app.core.skill_loader import load_skill, render_skill


def determine_cell_mapping(
    json_data: dict,
    workbook: Workbook,
    sheet_name: str,
    frame_name: str = "frameB",
) -> dict[str, list[str]]:
    """
    JSON データのキーを Excel のセル番地にマッピングする。

    固定レイアウト（YAML定義済み）フィールドは決定論的に解決。
    YAML未定義フィールドのみ LLM にフォールバック。
    リスト型フィールドはスキップ（tabular_handler が処理する）。
    """
    try:
        config = load_frame_config(frame_name, sheet_name)
        yaml_cell_defs = extract_cell_definitions(config)
        field_aliases = config.get("field_aliases", {})
        print(f"   YAML定義から {len(yaml_cell_defs)} フィールドを読み込みました")
    except FileNotFoundError:
        yaml_cell_defs = {}
        field_aliases = {}
        print("   YAML定義なし → スキャナーのみ使用")

    result: dict[str, list[str]] = {}
    unknown_keys: list[str] = []

    for key, value in json_data.items():
        # リスト型は tabular_handler が処理するためスキップ
        if isinstance(value, list):
            continue

        # YAML定義で確定するフィールド
        if key in yaml_cell_defs:
            result[key] = yaml_cell_defs[key]
            print(f"   {key} → {yaml_cell_defs[key]}（YAML定義）")
            continue

        # field_aliases で解決できるか確認
        resolved = _resolve_by_alias(key, yaml_cell_defs, field_aliases)
        if resolved:
            result[key] = resolved
            print(f"   {key} → {resolved}（エイリアス解決）")
            continue

        # YAML未定義 → LLM で解決が必要
        unknown_keys.append(key)

    # YAML未定義フィールドがある場合のみ LLM を呼び出す
    if unknown_keys:
        print(f"=== YAML未定義フィールド {unknown_keys} → AI判定 ===")
        unknown_data = {k: json_data[k] for k in unknown_keys}
        label_map = scan_label_cells(workbook, sheet_name)

        skill_dir = Path(__file__).parent
        skill_text = load_skill(skill_dir)
        prompt = render_skill(
            skill_text,
            json_data=json.dumps(unknown_data, ensure_ascii=False, indent=2),
            label_map=json.dumps(label_map, ensure_ascii=False, indent=2),
            yaml_cell_defs=json.dumps(yaml_cell_defs, ensure_ascii=False, indent=2),
            field_aliases=json.dumps(field_aliases, ensure_ascii=False, indent=2),
        )

        response_text = call_gemini(prompt)
        cleaned_text = _extract_json(response_text)

        try:
            ai_result = json.loads(cleaned_text)
            mappings = ai_result.get("mappings", {})
            reasoning = ai_result.get("reasoning", {})
            normalized = _normalize_mappings(mappings)

            for key, cells in normalized.items():
                reason = reasoning.get(key, "根拠なし")
                print(f"   {key} → {cells}（AI判定: {reason}）")

            result.update(normalized)

        except json.JSONDecodeError as e:
            print(f"AI 応答パース失敗: {e}")
            label_map_for_fallback = scan_label_cells(workbook, sheet_name) if not unknown_keys else label_map
            for k in unknown_keys:
                if k in label_map_for_fallback:
                    result[k] = label_map_for_fallback[k]
                    print(f"   {k} → {label_map_for_fallback[k]}（スキャナーフォールバック）")
                else:
                    result[k] = []
                    print(f"   {k} → 不明（解決できませんでした）")

    return result


def _resolve_by_alias(
    key: str,
    yaml_cell_defs: dict[str, list[str]],
    field_aliases: dict[str, list[str]],
) -> list[str] | None:
    """field_aliases を使ってキーを YAML 定義フィールドに解決する。"""
    for yaml_key, aliases in field_aliases.items():
        if key in aliases and yaml_key in yaml_cell_defs:
            return yaml_cell_defs[yaml_key]
    return None


def _extract_json(text: str) -> str:
    """AI の応答から JSON 部分のみを抽出する。"""
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0).strip()
    return text.strip()


def _normalize_mappings(mappings: dict) -> dict[str, list[str]]:
    """マッピングの値を必ずリスト型に統一する。"""
    normalized: dict[str, list[str]] = {}
    for key, val in mappings.items():
        if isinstance(val, list):
            normalized[key] = val
        elif isinstance(val, str):
            normalized[key] = [val]
        else:
            normalized[key] = []
    return normalized