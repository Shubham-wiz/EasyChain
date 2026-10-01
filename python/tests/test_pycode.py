from __future__ import annotations

import ast

import pytest

from easychain.compiler.pycode import Imports, Names, docstring, py_literal, py_regex, py_str


@pytest.mark.parametrize(
    "value",
    [
        "plain",
        'quote " and \\ backslash',
        "line1\nline2",
        'ends with quote"',
        'has """ triple\nquotes',
        "tab\tand\r\nwindows",
        "üñí ✓",
    ],
)
def test_py_str_round_trips(value):
    assert ast.literal_eval(py_str(value)) == value


def test_py_regex_prefers_raw_strings():
    assert py_regex(r"#\d+") == r'r"#\d+"'
    assert ast.literal_eval(py_regex('a"\\d')) == 'a"\\d'
    assert py_regex("plain") == '"plain"'


def test_py_literal_wraps_long_values():
    data = {
        "key": ["a" * 30, "b" * 30, {"nested": True, "n": None, "f": 1.5}],
        "empty": {},
        "list": [],
    }
    text = py_literal(data)
    assert "\n" in text
    assert ast.literal_eval(text) == data
    assert py_literal([1, "x"]) == '[1, "x"]'
    with pytest.raises(TypeError):
        py_literal(object())


def test_docstring_forms():
    assert docstring("One line") == '    """One line"""'
    multi = docstring('Title\n\nBody with """ quotes')
    assert multi.startswith('    """Title\n') and multi.endswith('    """')


def test_imports_render_groups_and_aliases():
    imports = Imports()
    imports.add("json")
    imports.add("numpy", "np")
    imports.add_from("typing", "Any", "Annotated")
    imports.add_from("langgraph.graph", "StateGraph", "END", "START", "add_messages")
    imports.add_from("__future__", "annotations")
    text = imports.render()
    assert text.split("\n\n")[0] == "from __future__ import annotations"
    assert "import json\nfrom typing import Annotated, Any" in text
    assert "from langgraph.graph import END, START, StateGraph, add_messages" in text
    assert "import numpy as np" in text


def test_names_avoid_reserved_and_keywords():
    names = Names()
    assert names.claim("graph") == "graph_2"
    assert names.claim("class") == "class_"
    assert names.claim("step") == "step"
    assert names.claim("step") == "step_2"
    names.reserve("taken")
    assert names.claim("taken") == "taken_2"
