"""
表形式セクションハンドラ

解体機器表のような複数行の表形式データを
Excel に書き込む処理を提供する。
"""
import logging

from openpyxl.workbook.workbook import Workbook

from apps.backend.app.core.cell_writer import write_to_cell
from apps.backend.app.core.unit_converter import convert_unit

logger = logging.getLogger(__name__)

MAX_ROWS_DEFAULT = 200


def write_tabular_section(
    workbook: Workbook,
    sheet_name: str,
    section_config: dict,
    data: dict,
    max_rows: int = MAX_ROWS_DEFAULT,
) -> None:
    """
    表形式データを Excel に書き込む。

    Args:
        workbook: 対象の Workbook オブジェクト
        sheet_name: 書き込み先シート名
        section_config: YAML のセクション定義
        data: 入力 JSON データ全体
        max_rows: 書き込む最大行数（安全弁。超過分は先頭 max_rows 行のみ）
    """
    json_key = section_config.get("json_key")
    data_start_row = section_config.get("data_start_row", 30)
    columns = section_config.get("columns", [])

    # JSON からリストデータを取得
    rows = data.get(json_key, [])
    if not rows:
        print(f"   ⚠️  {json_key} のデータが見つかりません")
        return

    # max_rows 安全弁
    if len(rows) > max_rows:
        logger.warning(
            f"[tabular_handler] {json_key} のデータ行数 {len(rows)} が上限 {max_rows} を超えています。"
            f"先頭 {max_rows} 行のみ書き込みます。"
        )
        rows = rows[:max_rows]

    # 列名 → (列アドレス, unit) のマップを作成
    col_map = {col["name"]: col["column"] for col in columns}
    col_unit_map = {col["name"]: col.get("unit") for col in columns}

    # 行ごとに書き込み
    for row_idx, row_data in enumerate(rows):
        excel_row = data_start_row + row_idx
        for field_name, value in row_data.items():
            if field_name not in col_map:
                continue
            if not value:
                continue

            # 書き込み直前の単位変換（費用列など unit: 千円 が定義された列のみ）
            col_unit = col_unit_map.get(field_name)
            if col_unit and col_unit != "円":
                converted = convert_unit(value, from_unit="円", to_unit=col_unit)
                if converted is None:
                    logger.warning(
                        f"[tabular_handler] {field_name} の単位変換失敗（元の値: {value}）。スキップ。"
                    )
                    continue
                value = converted

            col_letter = col_map[field_name]
            cell_address = f"{col_letter}{excel_row}"
            success = write_to_cell(
                workbook, sheet_name, cell_address, value
            )
            if success:
                print(f"   ✅ {field_name}({value}) → {cell_address}")
            else:
                print(f"   ❌ {field_name}({value}) → {cell_address} 失敗")