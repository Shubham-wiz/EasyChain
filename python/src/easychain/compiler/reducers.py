"""Custom update rules: a Flow Data field combines values with ``combine(old, new)``."""

from __future__ import annotations

import ast
import re

from .pycode import Imports

TEMPLATE = '''def combine(old, new):
    """Return the field's new value from the old one and the update."""
    return new
'''


def check_combine(code: str) -> str | None:
    """A plain-language problem with the code, or None when it is fine."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"Python can't read the update rule: {exc.msg} (line {exc.lineno})."
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "combine"]
    if not funcs:
        return "The update rule needs a function combine(old, new)."
    if len(funcs[0].args.args) != 2:
        return "combine takes exactly two arguments: combine(old, new)."
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.Import | ast.ImportFrom | ast.Expr):
            return "Only imports and the combine function can be in an update rule."
    return None


def rename_combine(code: str, name: str, imports: Imports) -> str:
    """Rename combine() to ``name`` and move its imports to the module's import block."""
    tree = ast.parse(code)
    lines = code.strip("\n").split("\n")
    drop: set[int] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name, alias.asname)
            drop.update(range(node.lineno - 1, (node.end_lineno or node.lineno)))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                imports.add_from(
                    node.module, f"{alias.name} as {alias.asname}" if alias.asname else alias.name
                )
            drop.update(range(node.lineno - 1, (node.end_lineno or node.lineno)))
        elif isinstance(node, ast.FunctionDef) and node.name == "combine":
            idx = node.lineno - 1
            lines[idx] = re.sub(r"\bdef\s+combine\s*\(", f"def {name}(", lines[idx], count=1)
    return "\n".join(line for i, line in enumerate(lines) if i not in drop).strip("\n")
