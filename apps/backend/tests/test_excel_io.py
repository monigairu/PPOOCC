"""
excel_io.py と cell_writer.py のテスト

実行方法:
    PYTHONPATH=. uv run pytest apps/backend/tests/test_excel_io.py -v
"""
import pytest
from apps.backend.app.core.excel_io import (
    load_workbook_file,
    save_workbook_file,
    copy_excel_file,
)
from apps.backend.app.core.cell_writer import write_to_cell, get_cell_value
from apps.backend.app.config.paths import template_workbook_path

TEMPLATE_PATH = str(template_workbook_path())
OUTPUT_PATH = "data/form_generation/output/test_result.xlsx"


def test_copy_excel_file():
    """テンプレートをコピーできるか"""
    copy_excel_file(TEMPLATE_PATH, OUTPUT_PATH)

    wb = load_workbook_file(OUTPUT_PATH)
    assert "MRC1" in wb.sheetnames


def test_write_and_read_cell():
    """セルへの書き込みと読み込みが正しいか"""
    copy_excel_file(TEMPLATE_PATH, OUTPUT_PATH)
    wb = load_workbook_file(OUTPUT_PATH)

    result = write_to_cell(wb, "MRC1", "C4", "2024")
    value = get_cell_value(wb, "MRC1", "C4")

    assert result is True       # 書き込みが成功したか
    assert value == "2024"      # 書いた値が読み取れるか


def test_save_and_reload():
    """保存後に再読み込みしても値が残っているか"""
    copy_excel_file(TEMPLATE_PATH, OUTPUT_PATH)
    wb = load_workbook_file(OUTPUT_PATH)
    write_to_cell(wb, "MRC1", "C4", "2024")
    save_workbook_file(wb, OUTPUT_PATH)

    # 保存後に別インスタンスで読み直す
    wb2 = load_workbook_file(OUTPUT_PATH)
    assert get_cell_value(wb2, "MRC1", "C4") == "2024"


def test_write_to_invalid_sheet():
    """存在しないシート名を指定したらFalseが返るか"""
    copy_excel_file(TEMPLATE_PATH, OUTPUT_PATH)
    wb = load_workbook_file(OUTPUT_PATH)

    result = write_to_cell(wb, "存在しないシート", "C4", "2024")
    assert result is False