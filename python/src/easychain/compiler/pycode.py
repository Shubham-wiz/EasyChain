"""Small helpers for writing readable Python source."""

from __future__ import annotations

import json
import keyword
import sys
import textwrap
from typing import Any


def py_str(value: str) -> str:
    """A double-quoted Python string literal; triple-quoted when it spans lines."""
    if "\n" not in value:
        return json.dumps(value, ensure_ascii=False)
    body = value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"').replace("\r", "\\r")
    if body.endswith('"'):
        body = body[:-1] + '\\"'
    return '"""\\\n' + body + '"""'


def py_regex(value: str) -> str:
    """A raw string literal when that reads better (regular expressions)."""
    if "\\" in value and '"' not in value and "\n" not in value and not value.endswith("\\"):
        return f'r"{value}"'
    return py_str(value)


class RawCode(str):
    """Source code to place as it is, where py_literal would otherwise quote a string."""


def py_literal(value: Any, indent: int = 0) -> str:
    """Render JSON-like data as a Python literal with double quotes."""
    if isinstance(value, RawCode):
        return str(value)
    pad = " " * indent
    inner = " " * (indent + 4)
    if isinstance(value, str):
        return py_str(value)
    if value is None or isinstance(value, bool):
        return repr(value)
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, dict):
        if not value:
            return "{}"
        flat = "{" + ", ".join(f"{py_literal(k)}: {py_literal(v)}" for k, v in value.items()) + "}"
        if len(flat) + indent <= 80 and "\n" not in flat:
            return flat
        items = ",\n".join(
            f"{inner}{py_literal(k)}: {py_literal(v, indent + 4)}" for k, v in value.items()
        )
        return "{\n" + items + ",\n" + pad + "}"
    if isinstance(value, list | tuple):
        if not value:
            return "[]"
        flat = "[" + ", ".join(py_literal(v) for v in value) + "]"
        if len(flat) + indent <= 80 and "\n" not in flat:
            return flat
        items = ",\n".join(f"{inner}{py_literal(v, indent + 4)}" for v in value)
        return "[\n" + items + ",\n" + pad + "]"
    raise TypeError(f"Can't render {type(value).__name__} as a Python literal")


def docstring(text: str, spaces: int = 4) -> str:
    """A docstring block at the given indentation."""
    text = text.strip().replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    pad = " " * spaces
    width = 96 - spaces
    if any(len(line) > width for line in text.split("\n")):
        text = "\n".join(
            textwrap.fill(line, width, break_long_words=False, break_on_hyphens=False)
            if len(line) > width
            else line
            for line in text.split("\n")
        )
    if "\n" not in text:
        return f'{pad}"""{text}"""'
    lines = text.split("\n")
    body = "\n".join((pad + line) if line.strip() else "" for line in lines[1:])
    return f'{pad}"""{lines[0]}\n{body}\n{pad}"""'


class Imports:
    """Collects imports and renders them grouped and sorted (stdlib first)."""

    def __init__(self) -> None:
        self._modules: set[str] = set()
        self._names: dict[str, set[str]] = {}

    def add(self, module: str, alias: str | None = None) -> None:
        self._modules.add(f"{module} as {alias}" if alias and alias != module else module)

    def add_from(self, module: str, *names: str) -> None:
        self._names.setdefault(module, set()).update(names)

    def render(self) -> str:
        groups: dict[int, list[str]] = {0: [], 1: [], 2: []}
        for module in self._modules:
            groups[self._group(module.split(" ")[0])].append(f"import {module}")
        for module, names in self._names.items():
            ordered = sorted(names, key=_isort_key)
            line = f"from {module} import {', '.join(ordered)}"
            if len(line) > 88:
                # isort/ruff style for long imports: one name per line in parentheses.
                line = f"from {module} import (\n" + "".join(f"    {n},\n" for n in ordered) + ")"
            groups[self._group(module)].append(line)
        for lines in groups.values():
            # isort/ruff style: plain imports first, then from-imports, each alphabetical.
            lines.sort(key=lambda line: (line.startswith("from"), line.split()[1].lower()))
        return "\n\n".join("\n".join(lines) for lines in groups.values() if lines)

    def _group(self, module: str) -> int:
        if module == "__future__":
            return 0
        root = module.split(".")[0]
        return 1 if root in sys.stdlib_module_names else 2


def _isort_key(name: str) -> tuple[int, str]:
    """isort's default order for imported names: CONSTANTS, then Classes, then functions."""
    base = name.split(" ")[0]
    if base.isupper() and len(base) > 1:
        return (0, base)
    if base[:1].isupper():
        return (1, base.lower())
    return (2, base.lower())


class Names:
    """Hands out unique module-level names."""

    RESERVED = {
        "json",
        "sys",
        "os",
        "re",
        "operator",
        "httpx",
        "Any",
        "Annotated",
        "TypedDict",
        "StateGraph",
        "START",
        "END",
        "init_chat_model",
        "ChatPromptTemplate",
        "MessagesPlaceholder",
        "AnyMessage",
        "add_messages",
        "FlowData",
        "FlowInput",
        "FlowOutput",
        "build_graph",
        "graph",
        "fill",
        "readable_text",
        "pick_exit",
        "merge_dicts",
        "INPUT_DEFAULTS",
        "EXAMPLE_INPUT",
        "InMemorySaver",
        "data",
        # Phase 3: agents, structured replies, Knowledge Bases, MCP
        "tool",
        "BaseTool",
        "create_agent",
        "HumanMessage",
        "AIMessage",
        "BaseModel",
        "Field",
        "Literal",
        "ValidationError",
        "OutputParserException",
        "ToolStrategy",
        "init_embeddings",
        "as_text",
        "tell_agent",
        "memory_tools",
        "mcp_tools",
        "run_tool",
        "agent",
        "result",
        "np",
        "sa",
        "search_knowledge",
        "cite_passages",
        "rerank_passages",
        "KeywordEmbeddings",
        "Embeddings",
        "knowledge_engine",
        "knowledge_words",
        "knowledge_by_meaning",
        "knowledge_by_words",
        "KNOWLEDGE_ENGINES",
        "KNOWLEDGE_VECTORS",
        "hits",
        "embeddings",
    }

    def __init__(self) -> None:
        self._used: set[str] = set(self.RESERVED)

    def claim(self, base: str) -> str:
        name = base if base.isidentifier() and not keyword.iskeyword(base) else f"{base}_"
        candidate, n = name, 2
        while candidate in self._used:
            candidate = f"{name}_{n}"
            n += 1
        self._used.add(candidate)
        return candidate

    def reserve(self, name: str) -> None:
        self._used.add(name)
