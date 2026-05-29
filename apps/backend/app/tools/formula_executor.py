"""
汎用計算エグゼキューター

Gemini が資料から抽出した計算仕様（FormulaSpec）を受け取り、
Python の AST ウォークで安全に再計算・検証する。
特定の計算式（歩掛など）はハードコードしない。

【設計上の注意】
このモジュールは Gemini の「算術ミス」を検出するが、
「係数や変数の読み取りミス」は検出しない。
FormulaResult.source_location を必ず人間レビューの導線に含めること。
"""
import ast
import math
import operator
from dataclasses import dataclass, field


@dataclass
class FormulaSpec:
    """Gemini が資料から抽出した計算仕様"""
    formula_name: str
    expression: str              # 例: "ceil(weight * manhour_per_ton)"
    variables: dict[str, float]  # 例: {"weight": 1.5, "manhour_per_ton": 2.78}
    gemini_result: float         # Gemini が申告した計算結果
    result_unit: str             # 例: "人日", "円"
    source_location: dict = field(default_factory=dict)  # 抽出元情報


@dataclass
class FormulaResult:
    """formula_executor の検証結果"""
    formula_name: str
    python_result: float
    gemini_result: float
    is_consistent: bool
    result_unit: str
    needs_review: bool
    discrepancy_note: str | None
    source_location: dict = field(default_factory=dict)


_ALLOWED_OPERATORS = {
    ast.Add:  operator.add,
    ast.Sub:  operator.sub,
    ast.Mult: operator.mul,
    ast.Div:  operator.truediv,
    ast.Pow:  operator.pow,
    ast.USub: operator.neg,
}

_ALLOWED_FUNCTIONS = {
    "round": round,
    "ceil":  math.ceil,
    "floor": math.floor,
    "min":   min,
    "max":   max,
}


def safe_eval(expression: str, variables: dict[str, float]) -> float:
    """
    四則演算・べき乗と許可関数（round/ceil/floor/min/max）のみを許可する安全な数式評価器。
    exec/eval は使わず AST を手動でウォークする。

    例:
        safe_eval("ceil(weight * manhour_per_ton)", {"weight": 1.5, "manhour_per_ton": 2.78})
        → 5.0
    """
    tree = ast.parse(expression, mode="eval")
    return _eval_node(tree.body, variables)


def _eval_node(node, variables: dict) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)

    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise ValueError(
                f"変数 '{node.id}' が variables に見つかりません: {list(variables.keys())}"
            )
        return float(variables[node.id])

    if isinstance(node, ast.BinOp):
        op_func = _ALLOWED_OPERATORS.get(type(node.op))
        if not op_func:
            raise ValueError(f"許可されていない演算子: {type(node.op).__name__}")
        return op_func(_eval_node(node.left, variables), _eval_node(node.right, variables))

    if isinstance(node, ast.UnaryOp):
        op_func = _ALLOWED_OPERATORS.get(type(node.op))
        if not op_func:
            raise ValueError(f"許可されていない演算子: {type(node.op).__name__}")
        return op_func(_eval_node(node.operand, variables))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("メソッド呼び出しは許可されていません")
        func_name = node.func.id
        func = _ALLOWED_FUNCTIONS.get(func_name)
        if not func:
            raise ValueError(
                f"許可されていない関数: {func_name}。許可リスト: {list(_ALLOWED_FUNCTIONS)}"
            )
        args = [_eval_node(arg, variables) for arg in node.args]
        return float(func(*args))

    raise ValueError(f"許可されていない構文: {type(node).__name__}")


def execute_formula(spec: FormulaSpec, tolerance: float = 1e-2) -> FormulaResult:
    """
    FormulaSpec を Python で再計算し、Gemini の申告値と照合する。

    tolerance（デフォルト 1%）を超える相対誤差があれば needs_review=True。
    呼び出し元は needs_review=True を conflicts に積んで人間確認に回すこと。
    """
    try:
        python_result = safe_eval(spec.expression, spec.variables)
    except (ValueError, ZeroDivisionError) as e:
        return FormulaResult(
            formula_name=spec.formula_name,
            python_result=float("nan"),
            gemini_result=spec.gemini_result,
            is_consistent=False,
            result_unit=spec.result_unit,
            needs_review=True,
            discrepancy_note=f"計算式の評価エラー: {e}",
            source_location=spec.source_location,
        )

    if spec.gemini_result != 0:
        relative_error = abs(python_result - spec.gemini_result) / abs(spec.gemini_result)
        is_consistent = relative_error <= tolerance
    else:
        is_consistent = abs(python_result) <= tolerance

    return FormulaResult(
        formula_name=spec.formula_name,
        python_result=python_result,
        gemini_result=spec.gemini_result,
        is_consistent=is_consistent,
        result_unit=spec.result_unit,
        needs_review=not is_consistent,
        discrepancy_note=(
            f"Python={python_result:.4f} vs Gemini={spec.gemini_result:.4f}"
            if not is_consistent
            else None
        ),
        source_location=spec.source_location,
    )
