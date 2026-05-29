import pytest
from apps.backend.app.core.unit_converter import convert_unit, parse_to_float


# ── parse_to_float ───────────────────────────────────────────────────────────

def test_parse_int():
    assert parse_to_float(143500000) == 143_500_000.0


def test_parse_float():
    assert parse_to_float(1.5) == 1.5


def test_parse_comma_string():
    assert parse_to_float("143,500,000") == pytest.approx(143_500_000.0)


def test_parse_string_with_yen_symbol():
    assert parse_to_float("143500000円") == pytest.approx(143_500_000.0)


def test_parse_none_returns_none():
    assert parse_to_float(None) is None


def test_parse_invalid_string_returns_none():
    assert parse_to_float("非数値テキスト") is None


def test_parse_empty_string_returns_none():
    assert parse_to_float("") is None


# ── convert_unit ─────────────────────────────────────────────────────────────

def test_convert_yen_to_senyen():
    result = convert_unit(143_500_000, from_unit="円", to_unit="千円")
    assert result == pytest.approx(143_500.0)


def test_convert_yen_to_manen():
    result = convert_unit(143_500_000, from_unit="円", to_unit="万円")
    assert result == pytest.approx(14_350.0)


def test_convert_same_unit_returns_numeric():
    result = convert_unit(12345, from_unit="円", to_unit="円")
    assert result == pytest.approx(12345.0)


def test_convert_string_value():
    # validator.py がカンマ付き文字列で返してきても変換できること
    result = convert_unit("143,500,000", from_unit="円", to_unit="千円")
    assert result == pytest.approx(143_500.0)


def test_convert_none_returns_none():
    assert convert_unit(None, from_unit="円", to_unit="千円") is None


def test_convert_invalid_string_returns_none():
    assert convert_unit("非数値テキスト", from_unit="円", to_unit="千円") is None


def test_convert_unsupported_unit_raises():
    with pytest.raises(ValueError, match="未対応の変換"):
        convert_unit(1000, from_unit="円", to_unit="ドル")


def test_convert_zero():
    assert convert_unit(0, from_unit="円", to_unit="千円") == pytest.approx(0.0)


def test_convert_fractional_result():
    # 1500円 → 千円 = 1.5
    result = convert_unit(1500, from_unit="円", to_unit="千円")
    assert result == pytest.approx(1.5)
