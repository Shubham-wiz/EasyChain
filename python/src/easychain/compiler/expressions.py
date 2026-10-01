"""Safe Decision expressions.

Pro users can route on an expression such as ``len(page) > 5000 and "error" not in page``.
Bare names refer to Flow Data fields. Only a small, side-effect-free subset of
Python is allowed; everything else is rejected with a readable message. The
expression is rewritten so that ``page`` becomes ``data.get("page")``.
"""

from __future__ import annotations

import ast
import json

ALLOWED_FUNCS = {"len", "str", "int", "float", "bool", "abs", "min", "max", "round", "any", "all"}
ALLOWED_METHODS = {
    "lower",
    "upper",
    "strip",
    "startswith",
    "endswith",
    "get",
    "keys",
    "values",
    "count",
    "split",
    "isdigit",
}
_CONSTANT_NAMES = {"True", "False", "None"}

_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Call,
    ast.Attribute,
    ast.Subscript,
    ast.List,
    ast.Tuple,
    ast.IfExp,
    ast.Slice,
)


class ExpressionError(ValueError):
    pass


def parse_expression(expression: str) -> ast.Expression:
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"The expression isn't valid: {exc.msg}.") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(
                f"Expressions can't use {type(node).__name__} here. Use comparisons, "
                "and/or/not, arithmetic and simple functions like len()."
            )
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id not in ALLOWED_FUNCS:
                    raise ExpressionError(
                        f"The function {func.id}() isn't allowed. Allowed: "
                        + ", ".join(sorted(ALLOWED_FUNCS))
                        + "."
                    )
            elif isinstance(func, ast.Attribute):
                if func.attr not in ALLOWED_METHODS:
                    raise ExpressionError(f"The method .{func.attr}() isn't allowed.")
            else:
                raise ExpressionError("Only simple function calls are allowed.")
            if node.keywords:
                raise ExpressionError("Keyword arguments aren't allowed in expressions.")
        if isinstance(node, ast.Attribute) and (
            node.attr.startswith("_") or node.attr not in ALLOWED_METHODS
        ):
            raise ExpressionError(f"Attribute .{node.attr} isn't allowed.")
    return tree


def field_names(expression: str) -> set[str]:
    """Flow Data fields an expression reads."""
    tree = parse_expression(expression)
    called = {
        id(n.func)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    return {
        n.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and id(n) not in called and n.id not in _CONSTANT_NAMES
    }


def _dq(value: str) -> ast.Name:
    # ast.unparse writes repr() (single quotes); a Name holding the literal keeps double quotes.
    return ast.Name(id=json.dumps(value, ensure_ascii=False), ctx=ast.Load())


class _ToDataGet(ast.NodeTransformer):
    def __init__(self, call_targets: set[int]):
        self.call_targets = call_targets

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        return _dq(node.value) if isinstance(node.value, str) else node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if id(node) in self.call_targets or node.id in _CONSTANT_NAMES:
            return node
        return ast.Call(
            func=ast.Attribute(
                value=ast.Name(id="data", ctx=ast.Load()), attr="get", ctx=ast.Load()
            ),
            args=[_dq(node.id)],
            keywords=[],
        )


def compile_expression(expression: str) -> str:
    """Return Python source for the expression with fields read from ``data``."""
    tree = parse_expression(expression)
    call_targets = {
        id(n.func)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    new_tree = ast.fix_missing_locations(_ToDataGet(call_targets).visit(tree))
    return ast.unparse(new_tree)
