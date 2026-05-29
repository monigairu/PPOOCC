"""
MRC1 書き込み関連のテスト

writable:false スキップ・単位変換・max_rows 安全弁を確認する。
"""
import logging
import pytest
from unittest.mock import MagicMock, patch, call

from apps.backend.app.section_handlers.tabular_handler import write_tabular_section


# ─── tabular_handler: 基本書き込み ────────────────────────────────────────────

def _make_section(columns: list[dict], json_key: str = "解体機器リスト", data_start_row: int = 30) -> dict:
    return {
        "name": "解体機器表",
        "type": "tabular",
        "json_key": json_key,
        "data_start_row": data_start_row,
        "columns": columns,
    }


def test_tabular_basic_write():
    """列定義に従って write_to_cell が呼ばれること"""
    mock_wb = MagicMock()
    section = _make_section([{"name": "解体機器", "column": "D"}])
    data = {"解体機器リスト": [{"解体機器": "配管（50A）"}]}

    with patch("apps.backend.app.section_handlers.tabular_handler.write_to_cell", return_value=True) as mock_write:
        write_tabular_section(mock_wb, "MRC1", section, data)

    mock_write.assert_called_once_with(mock_wb, "MRC1", "D30", "配管（50A）")


def test_tabular_empty_data_skips():
    """データが空の場合は write_to_cell を呼ばない"""
    mock_wb = MagicMock()
    section = _make_section([{"name": "解体機器", "column": "D"}])
    data = {"解体機器リスト": []}

    with patch("apps.backend.app.section_handlers.tabular_handler.write_to_cell") as mock_write:
        write_tabular_section(mock_wb, "MRC1", section, data)

    mock_write.assert_not_called()


# ─── tabular_handler: max_rows 安全弁 ────────────────────────────────────────

def test_tabular_max_rows_truncates(caplog):
    """max_rows を超えた場合は先頭 max_rows 行のみ書き込む"""
    mock_wb = MagicMock()
    section = _make_section([{"name": "解体機器", "column": "D"}])
    data = {"解体機器リスト": [{"解体機器": f"機器{i}"} for i in range(10)]}

    with patch("apps.backend.app.section_handlers.tabular_handler.write_to_cell", return_value=True) as mock_write:
        with caplog.at_level(logging.WARNING):
            write_tabular_section(mock_wb, "MRC1", section, data, max_rows=5)

    assert mock_write.call_count == 5
    assert "上限" in caplog.text


def test_tabular_max_rows_no_warning_within_limit(caplog):
    """max_rows 以下の場合は WARNING を出さない"""
    mock_wb = MagicMock()
    section = _make_section([{"name": "解体機器", "column": "D"}])
    data = {"解体機器リスト": [{"解体機器": f"機器{i}"} for i in range(3)]}

    with patch("apps.backend.app.section_handlers.tabular_handler.write_to_cell", return_value=True):
        with caplog.at_level(logging.WARNING):
            write_tabular_section(mock_wb, "MRC1", section, data, max_rows=5)

    assert "上限" not in caplog.text


# ─── tabular_handler: 費用列の単位変換 ───────────────────────────────────────

def test_tabular_unit_conversion_cost_column():
    """unit: 千円 の列は円 → 千円 に変換して書き込む"""
    mock_wb = MagicMock()
    section = _make_section([
        {"name": "計画_費用", "column": "J", "unit": "千円"},
    ])
    data = {"解体機器リスト": [{"計画_費用": 5_000_000}]}

    with patch("apps.backend.app.section_handlers.tabular_handler.write_to_cell", return_value=True) as mock_write:
        write_tabular_section(mock_wb, "MRC1", section, data)

    mock_write.assert_called_once_with(mock_wb, "MRC1", "J30", 5000.0)


def test_tabular_no_unit_column_no_conversion():
    """unit が未定義の列は変換しない"""
    mock_wb = MagicMock()
    section = _make_section([{"name": "計画_員数", "column": "G"}])
    data = {"解体機器リスト": [{"計画_員数": 3}]}

    with patch("apps.backend.app.section_handlers.tabular_handler.write_to_cell", return_value=True) as mock_write:
        write_tabular_section(mock_wb, "MRC1", section, data)

    mock_write.assert_called_once_with(mock_wb, "MRC1", "G30", 3)


def test_tabular_unit_conversion_failure_skips(caplog):
    """単位変換が失敗した場合はその列をスキップして WARNING を出す"""
    mock_wb = MagicMock()
    section = _make_section([{"name": "計画_費用", "column": "J", "unit": "千円"}])
    data = {"解体機器リスト": [{"計画_費用": "変換不能な値abc"}]}

    with patch("apps.backend.app.section_handlers.tabular_handler.write_to_cell", return_value=True) as mock_write:
        with caplog.at_level(logging.WARNING):
            write_tabular_section(mock_wb, "MRC1", section, data)

    mock_write.assert_not_called()
    assert "単位変換失敗" in caplog.text


# ─── tabular_handler: 列定義にない列は無視 ───────────────────────────────────

def test_tabular_unknown_column_ignored():
    """YAML 列定義にないフィールドは書き込まない（差分列保護）"""
    mock_wb = MagicMock()
    section = _make_section([{"name": "解体機器", "column": "D"}])
    data = {"解体機器リスト": [{"解体機器": "配管（50A）", "差分_費用": 999}]}

    with patch("apps.backend.app.section_handlers.tabular_handler.write_to_cell", return_value=True) as mock_write:
        write_tabular_section(mock_wb, "MRC1", section, data)

    # 定義された列のみ書き込まれること
    assert mock_write.call_count == 1
    mock_write.assert_called_once_with(mock_wb, "MRC1", "D30", "配管（50A）")


# ─── writable:false スキップ（generate_form_from_dict 経由ではなく直接確認）───

def test_writable_false_fields_in_yaml():
    """MRC1.yaml の writable:false フィールドが正しく設定されていること"""
    import yaml
    from pathlib import Path

    yaml_path = Path("frames/frameB/MRC1.yaml")
    config = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    schema = config.get("extraction_schema", {})

    # 確定済みの writable:false フィールド
    assert schema["総額"].get("writable") is False
    assert schema["全体支払い対象金額"].get("writable") is False


def test_writable_true_by_default():
    """writable が未定義のフィールドはデフォルト true 扱いであること"""
    import yaml
    from pathlib import Path

    yaml_path = Path("frames/frameB/MRC1.yaml")
    config = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    schema = config.get("extraction_schema", {})

    # 工事件名は writable が定義されていない → True 扱い
    assert schema["工事件名"].get("writable", True) is True


def test_tabular_unit_in_yaml():
    """MRC1.yaml の 計画_費用・実績_費用 に unit: 千円 が設定されていること"""
    import yaml
    from pathlib import Path

    yaml_path = Path("frames/frameB/MRC1.yaml")
    config = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    sections = config.get("sections", [])

    tabular = next((s for s in sections if s.get("type") == "tabular"), None)
    assert tabular is not None

    col_units = {col["name"]: col.get("unit") for col in tabular.get("columns", [])}
    assert col_units.get("計画_費用") == "千円"
    assert col_units.get("実績_費用") == "千円"
    # 変換不要な列には unit がないこと
    assert col_units.get("計画_員数") is None
