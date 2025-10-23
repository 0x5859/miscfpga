"""
Safe mathematical expression sampling for custom DDS waveforms.
"""

from __future__ import annotations

import ast
import math
from typing import Callable, Dict, Iterable, List

_ALLOWED_FUNCS: Dict[str, Callable[..., float]] = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "sqrt": math.sqrt,
    "abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
}

_ALLOWED_NAMES = {"x", "t", "pi", "e"}

_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.USub,
    ast.UAdd,
)

_FULL_SCALE = 8191


class SafeExpressionError(ValueError):
    """Raised when the user expression contains unsupported syntax."""


def _validate_tree(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise SafeExpressionError(f"Unsupported syntax node: {type(node).__name__}")

        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES and node.id not in _ALLOWED_FUNCS:
            raise SafeExpressionError(f"Unsupported symbol: {node.id}")

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise SafeExpressionError("Only direct function calls are allowed")
            if node.func.id not in _ALLOWED_FUNCS:
                raise SafeExpressionError(f"Unsupported function: {node.func.id}")


def _normalize_expression(expr: str) -> str:
    # Translate caret power notation into Python exponentiation.
    return expr.replace("^", "**")


def compile_expression(expr: str) -> Callable[[float, float], float]:
    normalized_expr = _normalize_expression(expr)
    tree = ast.parse(normalized_expr, mode="eval")
    _validate_tree(tree)
    code = compile(tree, "<dds-expression>", "eval")

    def evaluator(x: float, t: float) -> float:
        env = {"x": x, "t": t, "pi": math.pi, "e": math.e, **_ALLOWED_FUNCS}
        return float(eval(code, {"__builtins__": {}}, env))

    return evaluator


def sample_expression(expr: str, length: int) -> List[int]:
    evaluator = compile_expression(expr)
    values: List[float] = []

    for index in range(length):
        t = index / float(length)
        x = 2.0 * math.pi * t
        values.append(evaluator(x, t))

    peak = max((abs(v) for v in values), default=0.0)
    if peak == 0.0:
        return [0 for _ in range(length)]

    out: List[int] = []
    for value in values:
        normalized = max(-1.0, min(1.0, value / peak))
        quantized = int(round(normalized * _FULL_SCALE))
        quantized = max(-8192, min(8191, quantized))
        out.append(quantized)
    return out
