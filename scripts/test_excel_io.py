"""
excel_io.py と cell_writer.py の動作確認用スクリプト

使い方:
    python scripts/test_excel_io.py
"""
from src.core.excel_io import (
    load_workbook_file,
    save_workbook_file,
    copy_excel_file,
)
from src.core.cell_writer import write_to_cell, get_cell_value


def main():
    template_path = "data/form_generation/input/templates/frameB_MRC.xlsx"
    output_path = "data/form_generation/output/test_result.xlsx"

    # 1. テンプレートをコピー
    print("1. テンプレートをコピー中...")
    copy_excel_file(template_path, output_path)
    print(f"   コピー完了: {output_path}")

    # 2. コピーしたファイルを開く
    print("\n2. ファイルを開く...")
    wb = load_workbook_file(output_path)
    print(f"   シート一覧: {wb.sheetnames}")

    # 3. C4セルに「2024」と書き込み
    print("\n3. セルに書き込み...")
    success = write_to_cell(wb, "MRC1", "C4", "2024")
    print(f"   書き込み結果: {success}")

    # 4. 値を読み取って確認
    print("\n4. セルから読み込み...")
    value = get_cell_value(wb, "MRC1", "C4")
    print(f"   C4の値: {value}")

    # 5. 保存
    print("\n5. 保存中...")
    save_workbook_file(wb, output_path)
    print(f"   保存完了: {output_path}")

    print("\n=== 動作確認完了 ===")


if __name__ == "__main__":
    main()