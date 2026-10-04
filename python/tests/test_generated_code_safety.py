"""Whatever people type into a flow, the generated code is valid Python that does only what the
flow says (fixes from the 2026-10-04 review)."""

from __future__ import annotations

import pytest

from easychain.compiler import CompileError, FlowAnalysis, codegen, compile_flow, validate
from easychain.compiler.pycode import Names, docstring
from easychain.runtime.loader import load_module

from .conftest import input_step, make_spec, output_step


def code_step(id_: str, code: str, name: str = "") -> dict:
    return {"id": id_, "type": "code", "name": name, "settings": {"code": code}}


def run_module(source: str) -> dict:
    """Load the generated module the way a run does, and build its graph."""
    module = load_module(source, "safety_test")
    module.build_graph()
    return module.__dict__


def test_a_name_with_line_breaks_stays_a_label():
    sneaky = "Tidy\nprint('INJECTED')\r\nimport os"
    spec = make_spec(
        [input_step("text"), code_step("tidy", "def run(data):\n    return {'out': 1}\n", sneaky)],
        [("input", "tidy")],
        name="Flow\nimport os",
    )
    assert spec.name == "Flow import os"
    assert spec.step("tidy").name == "Tidy print('INJECTED') import os"
    # An unconnected step's name goes into a comment too.
    spec.steps.append(spec.steps[1].model_copy(update={"id": "spare", "name": "x\nprint(1)"}))
    source = compile_flow(spec).source
    for line in source.splitlines():
        assert not line.startswith(("print(", "import os")), line


def test_docstrings_ending_in_a_quote_are_valid():
    for text in ('Classify it as "spam"', 'Say "hi"\nthen "bye"', '"""', "ends with \\"):
        compile(f"def f():\n{docstring(text)}\n", "x.py", "exec")


def test_python_words_are_refused_as_field_names():
    spec = make_spec(
        [
            input_step("from"),
            code_step("work", "def run(data):\n    return {'y': 1}\n"),
            output_step("y"),
        ],
        [("input", "work"), ("work", "output")],
    )
    found = [i for i in validate(spec) if i.code == "field_python_word"]
    assert found and found[0].step == "input" and "from" in found[0].message
    with pytest.raises(CompileError):
        compile_flow(spec)


def test_python_words_are_refused_in_reply_fields():
    spec = make_spec(
        [
            input_step("review"),
            {
                "id": "triage",
                "type": "ai_model",
                "settings": {
                    "prompt": "review",
                    "output": {"fields": [{"name": "class", "description": "The class."}]},
                },
            },
            output_step("answer"),
        ],
        [("input", "triage"), ("triage", "output")],
    )
    assert "schema_python_word" in {i.code for i in validate(spec)}


def test_steps_cant_take_names_the_helpers_use():
    reserved = Names.reserved()
    for name in ("quote", "interrupt", "run_once", "idempotency_key", "fill_url", "collect_items"):
        assert name in reserved
    names = Names()
    assert names.claim("quote") == "quote_2"


def test_a_web_request_step_called_quote_still_fills_its_url():
    spec = make_spec(
        [
            input_step("q"),
            {
                "id": "quote",
                "type": "http_request",
                "settings": {"url": "https://example.com/search?q={q}"},
            },
            output_step("response"),
        ],
        [("input", "quote"), ("quote", "output")],
    )
    source = compile_flow(spec).source
    module = run_module(source)
    assert module["fill_url"]("https://x/?q={q}", {"q": "a b"}) == "https://x/?q=a%20b"


def test_an_update_rule_may_start_with_blank_lines():
    spec = make_spec(
        [
            input_step("x"),
            code_step("work", "def run(data):\n    return {'tags': ['a']}\n"),
            output_step("tags"),
        ],
        [("input", "work"), ("work", "output")],
        data=[
            {
                "name": "tags",
                "type": "list",
                "update": "custom",
                "combine": "\n\ndef combine(old, new):\n    return (old or []) + new\n",
            }
        ],
    )
    run_module(compile_flow(spec).source)


def test_an_update_rule_cant_run_code_when_the_module_loads():
    spec = make_spec(
        [
            input_step("x"),
            code_step("work", "def run(data):\n    return {'tags': ['a']}\n"),
            output_step("tags"),
        ],
        [("input", "work"), ("work", "output")],
        data=[
            {
                "name": "tags",
                "type": "list",
                "update": "custom",
                "combine": "print('loaded')\n\ndef combine(old, new):\n    return new\n",
            }
        ],
    )
    assert "bad_update_rule" in {i.code for i in validate(spec)}


def test_code_reading_keys_that_cant_be_fields_still_compiles():
    tool = code_step("lookup", "def run(data):\n    return {'found': data.get('order-id')}\n")
    spec = make_spec(
        [
            input_step("question"),
            tool,
            {
                "id": "helper",
                "type": "agent",
                "settings": {"input": "question", "tools": ["lookup"]},
            },
            output_step("answer"),
        ],
        [("input", "helper"), ("helper", "output")],
    )
    an = FlowAnalysis(spec)
    assert "order-id" not in an.fields
    compile(compile_flow(spec, allow_errors=True).source, "x.py", "exec")


def test_code_python_cant_read_is_a_clear_problem_not_a_crash(monkeypatch):
    spec = make_spec(
        [
            input_step("x"),
            code_step("work", "def run(data):\n    return {'y': 1}\n"),
            output_step("y"),
        ],
        [("input", "work"), ("work", "output")],
    )
    real = codegen._emit_module

    def broken(*args, **kwargs):
        compiled = real(*args, **kwargs)
        compiled.source += "\ndef oops(:\n"
        return compiled

    monkeypatch.setattr(codegen, "_emit_module", broken)
    with pytest.raises(CompileError, match="Python can't read"):
        compile_flow(spec)
    assert "generated_code_invalid" in {
        i.code for i in compile_flow(spec, allow_errors=True).issues
    }
