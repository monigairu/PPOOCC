import math
import pytest
from apps.backend.app.tools.formula_executor import (
    FormulaSpec,
    FormulaResult,
    execute_formula,
    safe_eval,
)


# ── safe_eval ────────────────────────────────────────────────────────────────

def test_safe_eval_basic_arithmetic():
    assert safe_eval("a * b", {"a": 2.0, "b": 3.0}) == 6.0


def test_safe_eval_addition_subtraction():
    assert safe_eval("a + b - c", {"a": 10.0, "b": 3.0, "c": 2.0}) == 11.0


def test_safe_eval_division():
    assert safe_eval("a / b", {"a": 10.0, "b": 4.0}) == pytest.approx(2.5)


def test_safe_eval_power():
    assert safe_eval("a ** b", {"a": 2.0, "b": 3.0}) == 8.0


def test_safe_eval_unary_neg():
    assert safe_eval("-a", {"a": 5.0}) == -5.0


def test_safe_eval_literal_constant():
    assert safe_eval("2.5 * a", {"a": 4.0}) == 10.0


def test_safe_eval_allowed_functions_ceil():
    assert safe_eval("ceil(a)", {"a": 2.3}) == math.ceil(2.3)


def test_safe_eval_allowed_functions_floor():
    assert safe_eval("floor(a)", {"a": 2.9}) == math.floor(2.9)


def test_safe_eval_allowed_functions_round():
    assert safe_eval("round(a)", {"a": 2.5}) == round(2.5)


def test_safe_eval_allowed_functions_min_max():
    assert safe_eval("min(a, b)", {"a": 3.0, "b": 5.0}) == 3.0
    assert safe_eval("max(a, b)", {"a": 3.0, "b": 5.0}) == 5.0


def test_safe_eval_complex_expression():
    # 歩掛計算式のイメージ: ceil(重量 × 基準工数/t)
    result = safe_eval(
        "ceil(weight * manhour_per_ton)",
        {"weight": 1.5, "manhour_per_ton": 2.78},
    )
    assert result == math.ceil(1.5 * 2.78)


def test_safe_eval_undefined_variable_raises():
    with pytest.raises(ValueError, match="見つかりません"):
        safe_eval("a * undefined_var", {"a": 3.0})


def test_safe_eval_disallows_exec_eval():
    with pytest.raises((ValueError, Exception)):
        safe_eval("__import__('os').system('ls')", {})


def test_safe_eval_disallows_method_calls():
    with pytest.raises(ValueError):
        safe_eval("a.evil()", {"a": 1.0})


def test_safe_eval_disallows_unknown_function():
    with pytest.raises(ValueError, match="許可されていない関数"):
        safe_eval("abs(a)", {"a": -1.0})


def test_safe_eval_disallows_list_comprehension():
    with pytest.raises((ValueError, Exception)):
        safe_eval("[x for x in range(10)]", {})


# ── execute_formula ──────────────────────────────────────────────────────────

def _make_spec(**kwargs) -> FormulaSpec:
    defaults = dict(
        formula_name="テスト",
        expression="a * b",
        variables={"a": 3.0, "b": 4.0},
        gemini_result=12.0,
        result_unit="人日",
        source_location={},
    )
    return FormulaSpec(**{**defaults, **kwargs})


def test_execute_formula_consistent():
    result = execute_formula(_make_spec())
    assert result.is_consistent is True
    assert result.needs_review is False
    assert result.discrepancy_note is None
    assert result.python_result == pytest.approx(12.0)


def test_execute_formula_inconsistent_triggers_review():
    result = execute_formula(_make_spec(gemini_result=99.0))
    assert result.is_consistent is False
    assert result.needs_review is True
    assert result.discrepancy_note is not None


def test_execute_formula_within_tolerance():
    # 1% 以内なら consistent
    result = execute_formula(_make_spec(gemini_result=12.05), tolerance=0.01)
    assert result.is_consistent is True


def test_execute_formula_exceeds_tolerance():
    # 2% 超なら needs_review
    result = execute_formula(_make_spec(gemini_result=12.3), tolerance=0.01)
    assert result.is_consistent is False
    assert result.needs_review is True


def test_execute_formula_undefined_variable_returns_review():
    spec = _make_spec(expression="a * undefined_var", variables={"a": 3.0})
    result = execute_formula(spec)
    assert result.needs_review is True
    assert "エラー" in (result.discrepancy_note or "")


def test_execute_formula_source_location_propagated():
    loc = {"file": "calc.xlsx", "sheet": "配管基準工数", "row": 15}
    result = execute_formula(_make_spec(source_location=loc))
    assert result.source_location == loc


def test_execute_formula_zero_gemini_result():
    spec = _make_spec(expression="a - b", variables={"a": 4.0, "b": 4.0}, gemini_result=0.0)
    result = execute_formula(spec)
    assert result.is_consistent is True


def test_execute_formula_result_unit_preserved():
    result = execute_formula(_make_spec(result_unit="千円"))
    assert result.result_unit == "千円"
