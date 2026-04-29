"""
セル書き込みモジュール

Excel シート上の指定セルに値を書き込む処理を提供する。
"""
from openpyxl import Workbook


def write_to_cell(
    workbook: Workbook,
    sheet_name: str,
    cell_address: str,
    value: str,
) -> bool:
    """
    指定セルに値を書き込む。

    Args:
        workbook: 対象の Workbook オブジェクト
        sheet_name: 書き込み先シート名（例: "MRC1"）
        cell_address: セルアドレス（例: "C7"）
        value: 書き込む値

    Returns:
        書き込みが成功した場合 True、失敗した場合 False
    """
    try:
        sheet = workbook[sheet_name]
        sheet[cell_address] = value
        return True
    except Exception as e:
        print(f"セルへの書き込みエラー ({sheet_name}!{cell_address}): {e}")
        return False


def get_cell_value(
    workbook: Workbook,
    sheet_name: str,
    cell_address: str,
) -> str | None:
    """
    指定セルの値を取得する。

    Args:
        workbook: 対象の Workbook オブジェクト
        sheet_name: 読み込み元シート名
        cell_address: セルアドレス（例: "C7"）

    Returns:
        セルの値（文字列）。値がない場合は None
    """
    sheet = workbook[sheet_name]
    value = sheet[cell_address].value
    return str(value) if value is not None else None