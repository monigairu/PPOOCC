"""
テンプレート・結果レイアウト取得エンドポイント

GET /api/template               空テンプレートのレイアウトを返す（起動時に取得）
GET /api/result-layout/{id}     転記済み出力ファイルのレイアウトを返す（転記後に取得）
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string

router = APIRouter()

TEMPLATE_PATH = Path("data/form_generation/input/templates/frameB_MRC.xlsx")
OUTPUT_DIR = Path("data/form_generation/output")


@router.get("/template")
async def get_template_structure(sheet_name: str = "MRC1"):
    """空テンプレートのレイアウト情報をJSONで返す。起動時に一度だけ取得する。"""
    if not TEMPLATE_PATH.exists():
        raise HTTPException(status_code=404, detail="テンプレートファイルが見つかりません")
    return _read_excel_layout(str(TEMPLATE_PATH), sheet_name)


@router.get("/result-layout/{session_id}")
async def get_result_layout(
    session_id: str,
    frame_name: str = "frameB",
    sheet_name: str = "MRC1",
):
    """転記済み出力ファイルのレイアウト情報をJSONで返す。転記完了後に取得する。

    テンプレートと異なり、動的に追加された解体機器の行も含む。
    """
    result_path = OUTPUT_DIR / f"result_{frame_name}_{session_id}.xlsx"
    if not result_path.exists():
        result_path = OUTPUT_DIR / f"result_{sheet_name}_{session_id}.xlsx"
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="転記済みファイルが見つかりません")
    return _read_excel_layout(str(result_path), sheet_name)


def _read_excel_layout(file_path: str, sheet_name: str) -> dict:
    """ExcelファイルからグリッドビューのレイアウトJSONを生成する共通処理。"""
    wb = load_workbook(file_path, data_only=True)
    if sheet_name not in wb.sheetnames:
        raise HTTPException(status_code=404, detail=f"シート '{sheet_name}' が見つかりません")

    ws = wb[sheet_name]

    # セル一覧
    cells = []
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=ws.max_column):
        for cell in row:
            cells.append({
                "row": cell.row,
                "col": cell.column,
                "address": cell.coordinate,
                "value": str(cell.value) if cell.value is not None else None,
            })

    # 結合セル範囲
    merged = []
    existing_merges: set[tuple] = set()
    for r in ws.merged_cells.ranges:
        merged.append({
            "start_row": r.min_row,
            "start_col": r.min_col,
            "end_row": r.max_row,
            "end_col": r.max_col,
        })
        existing_merges.add((r.min_row, r.min_col, r.max_row, r.max_col))

    # K:N が結合されているのに G:J が結合されていない行を補完（テンプレートの非対称修正）
    for r in list(merged):
        if (r["start_col"] == 11 and r["end_col"] == 14
                and r["start_row"] == r["end_row"]):
            key = (r["start_row"], 7, r["end_row"], 10)
            if key not in existing_merges:
                merged.append({
                    "start_row": r["start_row"],
                    "start_col": 7,
                    "end_row": r["end_row"],
                    "end_col": 10,
                })
                existing_merges.add(key)

    # 列幅（Excel単位 → ピクセル近似）
    col_widths = {}
    for letter, dim in ws.column_dimensions.items():
        idx = column_index_from_string(letter)
        col_widths[str(idx)] = max(int((dim.width or 8) * 8), 40)

    # 行高さ
    row_heights = {}
    for num, dim in ws.row_dimensions.items():
        row_heights[str(num)] = max(int((dim.height or 15) * 1.33), 18)

    return {
        "sheet_name": sheet_name,
        "max_row": ws.max_row,
        "max_col": ws.max_column,
        "cells": cells,
        "merged_cells": merged,
        "col_widths": col_widths,
        "row_heights": row_heights,
    }
