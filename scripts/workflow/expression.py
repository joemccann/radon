"""Sandboxed expression evaluator for workflow filter / gate / order nodes.

Operators author small predicates such as ``score >= 60 and direction ==
'ACCUMULATION'``. We evaluate them against a per-row context dict, but we must
never let an operator-authored string reach ``eval`` with real builtins — that
would be remote code execution against the scheduler.

The evaluator walks a parsed ``ast`` and refuses anything that is not pure
arithmetic / comparison / boolean logic over names already present in the
context: no calls, no attribute access, no subscripting, no comprehensions, no
imports. An unknown name raises ``ExpressionError`` rather than silently
returning ``None`` so a typo in a rule surfaces loudly.
"""
from __future__ import annotations

import ast
import operator
from typing import Any, Mapping


class ExpressionError(ValueError):
    """Raised for a malformed, unsafe, or undefined-name expression."""


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: operator.not_,
}

_COMPARE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def evaluate_expression(expression: str, context: Mapping[str, Any]) -> Any:
    """Evaluate ``expression`` against ``context`` and return its value.

    Raises ``ExpressionError`` on a parse error, an unsupported node, an
    unsafe construct, or a reference to a name not in ``context``.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"could not parse expression: {expression!r}") from exc
    return _eval_node(tree.body, context)


def _eval_node(node: ast.AST, context: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        if node.id in context:
            return context[node.id]
        if node.id in ("True", "False", "None"):  # ast.Constant covers py3.8+, belt+braces
            return {"True": True, "False": False, "None": None}[node.id]
        raise ExpressionError(f"unknown name in expression: {node.id!r}")

    if isinstance(node, ast.BoolOp):
        return _eval_boolop(node, context)

    if isinstance(node, ast.UnaryOp):
        handler = _UNARY_OPS.get(type(node.op))
        if handler is None:
            raise ExpressionError("unsupported unary operator")
        return handler(_eval_node(node.operand, context))

    if isinstance(node, ast.BinOp):
        handler = _BIN_OPS.get(type(node.op))
        if handler is None:
            raise ExpressionError("unsupported binary operator")
        return handler(_eval_node(node.left, context), _eval_node(node.right, context))

    if isinstance(node, ast.Compare):
        return _eval_compare(node, context)

    raise ExpressionError(f"unsupported expression element: {type(node).__name__}")


def _eval_boolop(node: ast.BoolOp, context: Mapping[str, Any]) -> Any:
    values = [_eval_node(v, context) for v in node.values]
    if isinstance(node.op, ast.And):
        result: Any = True
        for value in values:
            result = value
            if not value:
                return value
        return result
    # Or
    result = False
    for value in values:
        result = value
        if value:
            return value
    return result


def _eval_compare(node: ast.Compare, context: Mapping[str, Any]) -> bool:
    left = _eval_node(node.left, context)
    for op, comparator in zip(node.ops, node.comparators):
        handler = _COMPARE_OPS.get(type(op))
        if handler is None:
            raise ExpressionError("unsupported comparison operator")
        right = _eval_node(comparator, context)
        if not handler(left, right):
            return False
        left = right
    return True
