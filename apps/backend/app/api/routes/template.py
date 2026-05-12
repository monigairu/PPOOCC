"""
テンプレート・結果レイアウト取得エンドポイント

GET /api/template               空テンプレートのレイアウトを返す（起動時に取得）
GET /api/result-layout/{id}     転記済み出力ファイルのレイアウトを返す（転記後に取得）
"""

from pathlib import Path
from fastapi import APIRouter, HTTPException, Path as FastAPIPath, Query
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string

from apps.backend.app.core.settings import TEMPLATE_PATH, OUTPUT_DIR

router = APIRouter()


@router.get("/template")
async def get_template_structure(
    sheet_name: str = Query("MRC1", pattern=r"^[a-zA-Z0-9_\-]+$")
):
    """空テンプレートのレイアウト情報をJSONで返す。起動時に一度だけ取得する。"""
    if not TEMPLATE_PATH.exists():
        raise HTTPException(status_code=404, detail="テンプレートファイルが見つかりません")
    # sheet_nameはExcel内の参照にのみに使用されるが、念のためバリデーション済み
    return _read_excel_layout(str(TEMPLATE_PATH), sheet_name)


@router.get("/result-layout/{session_id}")
async def get_result_layout(
    session_id: str = FastAPIPath(..., pattern=r"^[a-f0-9\-]{8,36}+$"),
    frame_name: str = Query("frameB", pattern=r"^[a-zA-Z0-9_\-]+$"),
    sheet_name: str = Query("MRC1", pattern=r"^[a-zA-Z0-9_\-]+$"),
):
    """転記済み出力ファイルのレイアウト情報をJSONで返す。転記完了後に取得する。

    テンプレートと異なり、動的に追加された解体機器の行も含む。
    """
    # pathlib.Path.nameを使用してサニタイズ(パストラバーサル対策)
    safe_frame = Path(frame_name).name
    safe_session = Path(session_id).name
    safe_sheet = Path(sheet_name).name
    
    result_path = OUTPUT_DIR / f"result_{safe_frame}_{safe_session}.xlsx"
    if not result_path.exists():
        # 旧形式（8文字session_id または sheet_name 使用）へのフォールバック
        result_path = OUTPUT_DIR / f"result_{safe_sheet}_{safe_session}.xlsx"
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="転記済みファイルが見つかりません") 
    return _read_excel_layout(str(result_path), safe_sheet)


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
    for r in ws.merged_cells.ranges:
        merged.append({
            "start_row": r.min_row,
            "start_col": r.min_col,
            "end_row": r.max_row,
            "end_col": r.max_col,
        })

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
