"""Checks before a run: each problem is detected, pinned to a step and phrased for people."""

from __future__ import annotations

import pytest

from easychain.compiler import CompileError, FlowAnalysis, compile_flow, validate
from easychain.compiler.expressions import ExpressionError, compile_expression, field_names
from easychain.compiler.templates import secrets, to_fstring_template, variables

from .conftest import input_step, make_spec, output_step


def codes(spec) -> dict[str, list[str | None]]:
    out: dict[str, list[str | None]] = {}
    for issue in validate(spec):
        out.setdefault(issue.code, []).append(issue.step)
    return out


def ai(id_: str = "ask", **settings) -> dict:
    return {"id": id_, "type": "ai_model", "settings": settings}


def instr(id_: str = "prompt", **settings) -> dict:
    return {"id": id_, "type": "instructions", "settings": settings}


def test_clean_flow_has_no_issues():
    spec = make_spec(
        [input_step("q"), ai(prompt="q"), output_step("answer")],
        [("input", "ask"), ("ask", "output")],
    )
    assert validate(spec) == []


def test_structure_problems():
    assert "no_input" in codes(make_spec([output_step()], []))
    spec = make_spec(
        [input_step("q"), {**input_step("r"), "id": "input2"}, ai(prompt="q")], [("input", "ask")]
    )
    found = codes(spec)
    assert found["multiple_inputs"] == ["input2"]
    assert "no_output" in found
    assert "input_not_connected" in codes(make_spec([input_step("q"), output_step()], []))
    assert "nothing_to_run" in codes(
        make_spec([input_step("q"), output_step()], [("input", "output")])
    )
    assert "input_to_output" in codes(
        make_spec([input_step("q"), output_step()], [("input", "output")])
    )


def test_connection_problems():
    spec = make_spec(
        [input_step("q"), ai(prompt="q"), output_step("answer")],
        [
            ("input", "ask"),
            ("ask", "output"),
            ("ask", "input"),
            ("output", "ask"),
            ("ask", "ghost"),
            ("ask", "ask"),
            ("input", "ask"),
        ],
    )
    found = codes(spec)
    for code in (
        "into_input",
        "out_of_output",
        "connection_missing_step",
        "self_loop",
        "duplicate_connection",
    ):
        assert code in found, code


def test_exit_label_on_plain_step_is_a_warning():
    spec = make_spec(
        [input_step("q"), ai(prompt="q"), output_step("answer")],
        [("input", "ask"), {"from": "ask", "to": "output", "exit": "Yes"}],
    )
    assert "exit_on_plain_step" in codes(spec)


def test_unreachable_steps_are_warned_and_left_out_of_code():
    spec = make_spec(
        [input_step("q"), ai(prompt="q"), instr("orphan", user="hi"), output_step("answer")],
        [("input", "ask"), ("ask", "output")],
    )
    assert codes(spec)["unreachable"] == ["orphan"]
    source = compile_flow(spec).source
    assert "def orphan" not in source
    assert "Not connected to Input, so not included: orphan (orphan)" in source


def test_missing_variable_suggests_the_closest_field():
    spec = make_spec(
        [input_step("page"), instr(user="Summarise {pgae}"), ai(), output_step("answer")],
        [("input", "prompt"), ("prompt", "ask"), ("ask", "output")],
    )
    issue = next(i for i in validate(spec) if i.code == "missing_field")
    assert issue.level == "error"
    assert issue.step == "prompt"
    assert issue.hint == "Did you mean `page`?"
    assert issue.fix.params == {"setting": "user", "from": "pgae", "to": "page"}


def test_field_set_only_later_is_a_warning():
    spec = make_spec(
        [input_step("q"), instr(user="{answer}"), ai(), output_step("answer")],
        [("input", "prompt"), ("prompt", "ask"), ("ask", "output")],
    )
    issue = next(i for i in validate(spec) if i.code == "missing_field")
    assert issue.level == "warning"


def test_ai_model_checks():
    spec = make_spec(
        [
            input_step("q"),
            ai("a1", model="gpt-4o", prompt="q", save_as="a"),
            ai("a2", model="mystery:model", prompt="q", save_as="b"),
            ai("a3", model="anthropic:claude-opus-5-5", prompt="q", temperature=0.5, save_as="c"),
            ai(
                "a4",
                model="anthropic:claude-haiku-4-5",
                prompt="q",
                reasoning_effort="high",
                save_as="d",
            ),
            output_step("a"),
        ],
        [("input", "a1"), ("a1", "a2"), ("a2", "a3"), ("a3", "a4"), ("a4", "output")],
    )
    found = codes(spec)
    assert found["model_format"] == ["a1"]
    assert found["unknown_provider"] == ["a2"]
    assert found["temperature_unsupported"] == ["a3"]
    assert found["reasoning_effort_unsupported"] == ["a4"]


def test_ai_model_with_nothing_to_read_offers_to_add_instructions():
    spec = make_spec(
        [input_step(), ai(), output_step("answer")], [("input", "ask"), ("ask", "output")]
    )
    issue = next(i for i in validate(spec) if i.code == "no_prompt")
    assert issue.fix.kind == "add_step"
    assert issue.fix.params == {"type": "instructions", "before": "ask"}


def test_instruction_checks():
    spec = make_spec(
        [
            input_step("q"),
            instr(system="", user=""),
            instr("p2", user="x", history="q"),
            output_step(),
        ],
        [("input", "prompt"), ("prompt", "p2"), ("p2", "output")],
    )
    found = codes(spec)
    assert found["empty_instructions"] == ["prompt"]
    assert found["history_not_messages"] == ["p2"]


def test_http_checks():
    spec = make_spec(
        [
            input_step("q"),
            {"id": "h1", "type": "http_request", "settings": {"url": "", "save_as": "r1"}},
            {
                "id": "h2",
                "type": "http_request",
                "settings": {"url": "example.com/{q}", "body": "x", "save_as": "r2"},
            },
            {
                "id": "h3",
                "type": "http_request",
                "settings": {"url": "https://x/{nope}", "save_as": "r3"},
            },
            output_step("r1"),
        ],
        [("input", "h1"), ("h1", "h2"), ("h2", "h3"), ("h3", "output")],
    )
    found = codes(spec)
    assert found["no_url"] == ["h1"]
    assert found["url_scheme"] == ["h2"]
    assert found["get_with_body"] == ["h2"]
    assert found["missing_field"] == ["h3"]


@pytest.mark.parametrize(
    "code,expected",
    [
        ("def run(data:\n", "code_invalid"),
        ("def other(data):\n    return {}\n", "code_invalid"),
        ("async def run(data):\n    return {}\n", "code_invalid"),
        ("def run(a, b):\n    return {}\n", "code_invalid"),
        ("def run(data):\n    result = {'x': 1}\n    return result\n", "code_writes_unknown"),
        ("def run(data):\n    return {'Bad Name': 1}\n", "code_bad_field"),
        ("def graph():\n    pass\n\ndef run(data):\n    return {}\n", "code_name_clash"),
        ("from os import *\n\ndef run(data):\n    return {}\n", "code_star_import"),
        ("import os as json\n\ndef run(data):\n    return {}\n", "code_name_clash"),
    ],
)
def test_code_checks(code, expected):
    spec = make_spec(
        [input_step("q"), {"id": "c", "type": "code", "settings": {"code": code}}, output_step()],
        [("input", "c"), ("c", "output")],
    )
    assert expected in codes(spec)


def test_code_writes_and_reads_are_inferred():
    code = "def run(data):\n    x = data['a'] + data.get('b', '')\n    if x:\n        return {'c': x}\n    return {'c': '', 'd': 1}\n"
    spec = make_spec(
        [
            input_step("a", "b"),
            {"id": "c1", "type": "code", "settings": {"code": code}},
            output_step("c"),
        ],
        [("input", "c1"), ("c1", "output")],
    )
    an = FlowAnalysis(spec)
    assert an.writes["c1"] == {"c": "any", "d": "any"}
    assert an.reads["c1"] == {"a", "b"}


def test_code_steps_sharing_helper_names_warn():
    code = "def helper():\n    return 1\n\n\ndef run(data):\n    return {'x': helper()}\n"
    spec = make_spec(
        [
            input_step("q"),
            {"id": "c1", "type": "code", "settings": {"code": code}},
            {"id": "c2", "type": "code", "settings": {"code": code}},
            output_step("x"),
        ],
        [("input", "c1"), ("c1", "c2"), ("c2", "output")],
    )
    assert codes(spec)["code_shared_names"] == ["c2"]


def decision(**settings) -> dict:
    return {"id": "d", "type": "decision", "settings": settings}


def test_decision_checks():
    spec = make_spec(
        [
            input_step("q"),
            decision(
                exits=[
                    {"label": "A", "when": {"field": "q", "op": "contains"}},
                    {"label": "a", "when": {"field": "q", "op": "greater_than", "value": "lots"}},
                    {"label": "B", "when": {"field": "zz", "op": "is_empty"}},
                    {"label": "C", "when": {"op": "equals", "value": 1}},
                    {"label": "D", "when": {"field": "q", "op": "matches", "value": "("}},
                    {"label": "E", "when": {"expression": "__import__('os')"}},
                    {"label": "F"},
                ]
            ),
            output_step(),
        ],
        [
            ("input", "d"),
            {"from": "d", "to": "output"},
            ("d", "Ghost", "output"),
            ("d", "A", "output"),
            ("d", "A", "output"),
        ],
    )
    found = codes(spec)
    for code in (
        "rule_no_value",
        "duplicate_exit",
        "rule_not_number",
        "missing_field",
        "rule_no_field",
        "bad_pattern",
        "bad_expression",
        "exit_no_rule",
        "decision_connection_no_exit",
        "unknown_exit",
        "exit_fan_out",
        "exit_unconnected",
    ):
        assert code in found, code


def test_ai_decision_checks():
    spec = make_spec(
        [input_step(), decision(mode="ai", exits=[], model="nope"), output_step()],
        [("input", "d"), ("d", "Otherwise", "output")],
    )
    found = codes(spec)
    assert "ai_no_exits" in found and "model_format" in found and "ai_no_input" in found


def test_no_rules_warning():
    spec = make_spec(
        [input_step("q"), decision(exits=[]), output_step()],
        [("input", "d"), ("d", "Otherwise", "output")],
    )
    assert "no_rules" in codes(spec)


def test_loops():
    endless = make_spec(
        [input_step("q"), ai("a", prompt="q"), ai("b", prompt="answer"), output_step()],
        [("input", "a"), ("a", "b"), ("b", "a")],
    )
    assert "loop_without_exit" in codes(endless)
    guarded = make_spec(
        [
            input_step("q"),
            ai("a", prompt="q"),
            decision(exits=[{"label": "Again", "when": {"field": "answer", "op": "is_empty"}}]),
            output_step(),
        ],
        [("input", "a"), ("a", "d"), ("d", "Again", "a"), ("d", "Otherwise", "output")],
    )
    assert "loop_no_guard" in codes(guarded)


def test_field_problems():
    spec = make_spec(
        [
            input_step("q"),
            ai("a1", prompt="q", save_as="x"),
            {
                "id": "c",
                "type": "code",
                "settings": {"code": "def run(data):\n    return {'answer': 1}\n"},
            },
            ai("answer", prompt="q"),
            output_step("x", "nope"),
        ],
        [("input", "a1"), ("a1", "c"), ("c", "answer"), ("answer", "output")],
        data=[
            {"name": "q", "type": "text", "update": "merge"},
            {"name": "x", "type": "object", "update": "add"},
        ],
    )
    found = codes(spec)
    assert found["output_unknown_field"] == ["output"]
    assert found["step_id_is_field"] == ["answer"]
    assert "merge_non_object" in found
    assert "add_odd_type" in found


def test_field_type_conflict():
    spec = make_spec(
        [
            input_step("q"),
            {"id": "h", "type": "http_request", "settings": {"url": "https://x", "save_as": "v"}},
            instr("p", user="{q}", save_as="v"),
            output_step("v"),
        ],
        [("input", "h"), ("h", "p"), ("p", "output")],
    )
    assert "field_type_conflict" in codes(spec)


def test_output_warnings():
    spec = make_spec([input_step("q"), ai(prompt="q"), output_step()], [("input", "ask")])
    found = codes(spec)
    assert "output_not_connected" in found
    issue = next(
        i
        for i in validate(
            make_spec(
                [input_step("q"), ai(prompt="q"), output_step()],
                [("input", "ask"), ("ask", "output")],
            )
        )
    )
    assert issue.code == "output_no_fields"
    assert issue.fix.params == {"key": "fields", "value": ["answer"]}


def test_input_checks():
    spec = make_spec(
        [input_step("q", "q"), ai(prompt="q"), output_step("answer")],
        [("input", "ask"), ("ask", "output")],
    )
    assert "duplicate_input" in codes(spec)
    spec = make_spec(
        [input_step(), ai(), output_step("answer")], [("input", "ask"), ("ask", "output")]
    )
    assert "no_input_fields" in codes(spec)
    spec = make_spec(
        [input_step("messages", mode="chat"), ai(), output_step()],
        [("input", "ask"), ("ask", "output")],
    )
    assert "messages_reserved" in codes(spec)


def test_compile_error_lists_problems():
    with pytest.raises(CompileError) as info:
        compile_flow(make_spec([output_step()], []))
    assert "needs an Input step" in str(info.value)


def test_upstream_output_skips_decisions_and_prefers_forward_edges():
    spec = make_spec(
        [
            input_step("q"),
            instr(user="{q}"),
            decision(exits=[{"label": "Yes", "when": {"field": "q", "op": "is_not_empty"}}]),
            ai(),
            output_step("answer"),
        ],
        [
            ("input", "prompt"),
            ("prompt", "d"),
            ("d", "Yes", "ask"),
            ("d", "Otherwise", "ask"),
            ("ask", "output"),
        ],
    )
    an = FlowAnalysis(spec)
    assert an.upstream_output("ask") == "prompt"
    summary = an.summary()
    assert summary["exits"]["d"] == ["Yes", "Otherwise"]
    assert {f["name"] for f in summary["fields"]} == {"q", "prompt", "answer"}


# ── templates and expressions ────────────────────────────────────────────────


def test_template_helpers():
    assert variables("Hi {name}, {name} {secret:KEY} {{x}} {1bad}") == ["name", "x"]
    assert secrets("Bearer {secret:API_KEY} {secret:API_KEY}") == ["API_KEY"]
    assert to_fstring_template('JSON {"a": 1} for {name}') == 'JSON {{"a": 1}} for {name}'


def test_expressions_compile_to_data_reads():
    assert (
        compile_expression("len(page) > 10 and 'x' not in page")
        == 'len(data.get("page")) > 10 and "x" not in data.get("page")'
    )
    assert field_names("max(a, b) > c.count('x')") == {"a", "b", "c"}


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os')",
        "open('x')",
        "a.__class__",
        "lambda: 1",
        "[x for x in y]",
        "a(",
        "f(x=1)",
        "a.b(1)",
        "a[0]()",
    ],
)
def test_unsafe_expressions_are_rejected(expr):
    with pytest.raises(ExpressionError):
        compile_expression(expr)
