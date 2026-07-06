"""
excel_scanner.py の動作確認用スクリプト

使い方:
    python scripts/check_excel_scanner.py
"""
import json

from apps.backend.app.core.excel_io import load_workbook_file
from apps.backend.app.core.excel_scanner import scan_label_cells
from apps.backend.app.config.paths import template_workbook_path


def main():
    template_path = str(template_workbook_path())
    sheet_name = "MRC1"

    print(f"テンプレートを読み込み中: {template_path}")
    workbook = load_workbook_file(template_path)

    print(f"シート '{sheet_name}' をスキャン中...\n")
    label_map = scan_label_cells(workbook, sheet_name)

    print("=== スキャン結果 ===")
    print(f"検出したラベル数: {len(label_map)}\n")

    # 見やすくJSON形式で表示
    print(json.dumps(label_map, ensure_ascii=False, indent=2))

    # 「炉型」だけピックアップして確認
    print("\n=== 炉型の候補セル ===")
    if "炉型" in label_map:
        print(f"炉型 → {label_map['炉型']}")
    else:
        print("'炉型' ラベルが見つかりませんでした")


if __name__ == "__main__":
    main()