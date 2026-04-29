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
    json_data: dict[str, str],
    workbook: Workbook,
    sheet_name: str,
    frame_name: str = "frameB",
) -> dict[str, list[str]]:
    """
    JSON データのキーを Excel のセル番地にマッピングする。

    YAML定義（正確）とExcelスキャン（補助）の両方を使う。
    """
    # 1. YAMLからセル定義を取得（正確な情報）
    try:
        config = load_frame_config(frame_name, sheet_name)
        yaml_cell_defs = extract_cell_definitions(config)
        field_aliases = config.get("field_aliases", {})
        print(f"   YAML定義から {len(yaml_cell_defs)} フィールドを読み込みました")
    except FileNotFoundError:
        yaml_cell_defs = {}
        field_aliases = {}
        print("   YAML定義なし → スキャナーのみ使用")

    # 2. Excelスキャンで補助情報を取得
    label_map = scan_label_cells(workbook, sheet_name)

    # 3. SKILL.md を読み込んでプロンプトを構築
    skill_dir = Path(__file__).parent
    skill_text = load_skill(skill_dir)
    prompt = render_skill(
        skill_text,
        json_data=json.dumps(json_data, ensure_ascii=False, indent=2),
        label_map=json.dumps(label_map, ensure_ascii=False, indent=2),
        yaml_cell_defs=json.dumps(yaml_cell_defs, ensure_ascii=False, indent=2),
        field_aliases=json.dumps(field_aliases, ensure_ascii=False, indent=2),
    )

    # 4. Gemini に問い合わせ
    print("=== AI 判定中 ===")
    response_text = call_gemini(prompt)

    # 5. レスポンスを JSON としてパース
    cleaned_text = _extract_json(response_text)

    try:
        result = json.loads(cleaned_text)
        mappings = result.get("mappings", {})
        reasoning = result.get("reasoning", {})

        normalized = _normalize_mappings(mappings)

        print("=== AI 判定結果 ===")
        for key, cells in normalized.items():
            reason = reasoning.get(key, "根拠なし")
            print(f"  {key} → {cells}（理由: {reason}）")

        return normalized

    except json.JSONDecodeError as e:
        print(f"AI の応答をパースできませんでした: {e}")
        print(f"AI 応答内容: {response_text}")
        return _fallback_mapping(json_data, yaml_cell_defs, label_map)


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


def _fallback_mapping(
    json_data: dict[str, str],
    yaml_cell_defs: dict[str, list[str]],
    label_map: dict[str, list[str]],
) -> dict[str, list[str]]:
    """
    AI 応答がパース失敗した場合のフォールバック処理。

    YAMLを優先し、なければスキャナー結果を使う。
    """
    print("=== フォールバック ===")
    fallback: dict[str, list[str]] = {}
    for key in json_data.keys():
        if key in yaml_cell_defs:
            fallback[key] = yaml_cell_defs[key]
            print(f"  {key} → {yaml_cell_defs[key]}（YAML定義）")
        elif key in label_map:
            fallback[key] = label_map[key]
            print(f"  {key} → {label_map[key]}（スキャナー）")
        else:
            fallback[key] = []
            print(f"  {key} → 不明")
    return fallback