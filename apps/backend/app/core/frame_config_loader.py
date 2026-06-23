"""
様式定義（YAML）読み込みモジュール

config/{frame_name}/{sheet_name}.yaml を読み込み、
セクション定義とフィールドのセル番地を返す。
（旧 frames/ から config/ へ移行。互換のため frames/ も後方探索する）
"""
import yaml
from pathlib import Path

# 様式定義の探索先（config/ を正、frames/ は後方互換）
_CONFIG_DIRS = (Path("config"), Path("frames"))


def load_frame_config(frame_name: str, sheet_name: str) -> dict:
    """
    様式定義YAMLを読み込む。

    Args:
        frame_name: 様式名（例: "frameB"）
        sheet_name: シート名（例: "MRC1"）

    Returns:
        YAML の内容を辞書として返す
    """
    candidates = [d / frame_name / f"{sheet_name}.yaml" for d in _CONFIG_DIRS]
    for yaml_path in candidates:
        if yaml_path.exists():
            with open(yaml_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
    raise FileNotFoundError(
        f"様式定義ファイルが見つかりません: {[str(p) for p in candidates]}"
    )


def extract_cell_definitions(config: dict) -> dict[str, list[str]]:
    """
    YAML設定からフィールド名とセル番地の対応を抽出する。

    基本情報1（label_value型）と基本情報2（plan_actual型）の
    両方に対応する。

    Args:
        config: load_frame_config() の戻り値

    Returns:
        {フィールド名: [セル番地のリスト]}
        例: {"炉型": ["C7", "G9", "K9"]}
    """
    cell_definitions: dict[str, list[str]] = {}

    for section in config.get("sections", []):
        section_type = section.get("type")
        fields = section.get("fields", {})

        if section_type == "label_value":
            # 基本情報1: 値が文字列（単一セル）
            for field_name, cell in fields.items():
                if field_name not in cell_definitions:
                    cell_definitions[field_name] = []
                cell_definitions[field_name].append(str(cell))

        elif section_type == "plan_actual":
            # 基本情報2: 値が {plan: XX, actual: YY}
            for field_name, cell_info in fields.items():
                if field_name not in cell_definitions:
                    cell_definitions[field_name] = []
                if isinstance(cell_info, dict):
                    if "plan" in cell_info:
                        cell_definitions[field_name].append(
                            str(cell_info["plan"])
                        )
                    if "actual" in cell_info:
                        cell_definitions[field_name].append(
                            str(cell_info["actual"])
                        )

    return cell_definitions