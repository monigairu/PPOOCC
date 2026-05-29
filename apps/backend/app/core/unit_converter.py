"""
単位変換ユーティリティ（純粋関数）

パイプライン全体を通じて金額は円で統一し、
MRC1 への書き込み直前にのみここで変換する。
"""
import re

UNIT_DIVISORS: dict[str, int] = {
    "千円": 1_000,
    "万円": 10_000,
}


def parse_to_float(value) -> float | None:
    """
    validator.py が文字列のまま返す数値を float に変換する。

    "143,500,000" / "143500000円" / 143500000 などを受け付ける。
    変換できない場合は None を返す（呼び出し元が conflicts に積む）。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[,，円千万億\s]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return None


def convert_unit(value, from_unit: str, to_unit: str) -> float | None:
    """
    書き込み直前の単位変換（円 → 千円など）。

    前提: value は円単位の数値（Gemini が円で返すよう指示してある）。
    None が返った場合は呼び出し元が WARNING ログを出して書き込みをスキップすること。

    例:
        convert_unit(143_500_000, from_unit="円", to_unit="千円") → 143_500.0
        convert_unit("143,500,000", from_unit="円", to_unit="千円") → 143_500.0
    """
    numeric = parse_to_float(value)
    if numeric is None:
        return None
    if from_unit == to_unit:
        return numeric
    divisor = UNIT_DIVISORS.get(to_unit)
    if divisor is None:
        raise ValueError(f"未対応の変換: {from_unit} → {to_unit}")
    return numeric / divisor
