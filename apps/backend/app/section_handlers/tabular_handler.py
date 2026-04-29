"""
表形式セクションハンドラ

解体機器表のような複数行の表形式データを
Excel に書き込む処理を提供する。
"""
from openpyxl.workbook.workbook import Workbook

from apps.backend.app.core.cell_writer import write_to_cell


def write_tabular_section(
    workbook: Workbook,
    sheet_name: str,
    section_config: dict,
    data: dict,
) -> None:
    """
    表形式データを Excel に書き込む。

    Args:
        workbook: 対象の Workbook オブジェクト
        sheet_name: 書き込み先シート名
        section_config: YAML のセクション定義
        data: 入力 JSON データ全体
    """
    json_key = section_config.get("json_key")
    data_start_row = section_config.get("data_start_row", 30)
    columns = section_config.get("columns", [])

    # JSON からリストデータを取得
    rows = data.get(json_key, [])
    if not rows:
        print(f"   ⚠️  {json_key} のデータが見つかりません")
        return

    # 列名 → 列アドレスのマップを作成
    col_map = {col["name"]: col["column"] for col in columns}

    # 行ごとに書き込み
    for row_idx, row_data in enumerate(rows):
        excel_row = data_start_row + row_idx
        for field_name, value in row_data.items():
            if field_name not in col_map:
                continue
            if not value:
                continue
            col_letter = col_map[field_name]
            cell_address = f"{col_letter}{excel_row}"
            success = write_to_cell(
                workbook, sheet_name, cell_address, value
            )
            if success:
                print(f"   ✅ {field_name}({value}) → {cell_address}")
            else:
                print(f"   ❌ {field_name}({value}) → {cell_address} 失敗")