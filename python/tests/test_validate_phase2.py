"""Checks for the Phase 2 steps and settings: each problem is found and pinned to its step."""

from __future__ import annotations

from easychain.compiler import FlowAnalysis, validate

from .conftest import input_step, make_spec, output_step


def issues(spec, resolve=None, flow_id=None):
    return validate(spec, analysis=FlowAnalysis(spec, resolve=resolve, flow_id=flow_id))


def codes(spec, **kw) -> dict[str, list[str | None]]:
    out: dict[str, list[str | None]] = {}
    for issue in issues(spec, **kw):
        out.setdefault(issue.code, []).append(issue.step)
    return out


def code_step(id_: str, body: str = "return {'y': 1}") -> dict:
    return {"id": id_, "type": "code", "settings": {"code": f"def run(data):\n    {body}\n"}}


def test_step_ids_with_double_underscores_are_refused():
    spec = make_spec(
        [input_step("x"), code_step("my__step"), output_step("y")],
        [("input", "my__step"), ("my__step", "output")],
    )
    found = [i for i in issues(spec) if i.code == "step_id_double_underscore"]
    assert found and found[0].fix.params == {"to": "my_step"}


def test_custom_update_rule_is_checked():
    base = [input_step("x"), code_step("work", "return {'tags': ['a']}"), output_step("tags")]
    conns = [("input", "work"), ("work", "output")]
    bad = make_spec(
        base, conns, data=[{"name": "tags", "type": "list", "update": "custom", "combine": "x = 1"}]
    )
    assert "bad_update_rule" in codes(bad)
    empty = make_spec(base, conns, data=[{"name": "tags", "type": "list", "update": "custom"}])
    assert "bad_update_rule" in codes(empty)
    good = make_spec(
        base,
        conns,
        data=[
            {
                "name": "tags",
                "type": "list",
                "update": "custom",
                "combine": "def combine(old, new):\n    return (old or []) + new\n",
            }
        ],
    )
    assert "bad_update_rule" not in codes(good)
    unused = make_spec(
        base,
        conns,
        data=[{"name": "tags", "type": "list", "combine": "def combine(o, n):\n    return n\n"}],
    )
    assert "update_code_unused" in codes(unused)


def _loop(max_rounds=None, when_max=None):
    settings = {"exits": [{"label": "Again", "when": {"field": "y", "op": "is_not_empty"}}]}
    if max_rounds:
        settings["max_rounds"] = max_rounds
    if when_max:
        settings["when_max"] = when_max
    return make_spec(
        [
            input_step("x"),
            code_step("work"),
            {"id": "again", "type": "decision", "settings": settings},
            output_step("y"),
        ],
        [
            ("input", "work"),
            ("work", "again"),
            ("again", "Again", "work"),
            ("again", "Otherwise", "output"),
        ],
    )


def test_loop_without_round_limit_offers_a_fix_on_its_decision():
    [warning] = [i for i in issues(_loop()) if i.code == "loop_no_guard"]
    assert warning.step == "again"
    assert warning.fix.kind == "set_setting" and warning.fix.params["key"] == "max_rounds"
    assert "loop_no_guard" not in codes(_loop(max_rounds=5))


def test_round_limit_exit_must_exist():
    assert codes(_loop(max_rounds=3, when_max="Nope"))["unknown_when_max"] == ["again"]
    assert "when_max_unused" in codes(_loop(when_max="Again"))


def test_ask_human_settings_are_checked():
    def ask(**settings):
        return make_spec(
            [
                input_step("draft"),
                {"id": "ask", "type": "ask_human", "settings": settings},
                output_step("human_answer"),
            ],
            [("input", "ask"), ("ask", "Approved", "output"), ("ask", "Rejected", "output")]
            if settings.get("kind", "approve") in ("approve", "edit")
            else [("input", "ask"), ("ask", "output")],
        )

    assert "no_edit_field" in codes(ask(kind="edit"))
    assert "missing_field" in codes(ask(kind="edit", field="nope"))
    assert "too_few_options" in codes(ask(kind="choose", options=["Only"]))
    assert "duplicate_option" in codes(ask(kind="choose", options=["A", "a"]))
    assert "missing_field" in codes(ask(question="About {missing}"))
    assert "secret_in_question" in codes(ask(question="Key {secret:API_KEY}"))
    assert "missing_field" in codes(ask(show=["nope"]))
    clean = codes(ask(question="OK to send {draft}?", show=["draft"]))
    assert not {"missing_field", "exit_unconnected", "decision_connection_no_exit"} & set(clean)


def test_ask_human_exits_must_be_used():
    spec = make_spec(
        [input_step("draft"), {"id": "ask", "type": "ask_human"}, output_step("human_answer")],
        [("input", "ask"), ("ask", "output")],
    )
    assert "decision_connection_no_exit" in codes(spec)


def _for_each(body: dict, extra_conns=()):
    return make_spec(
        [
            input_step({"name": "items", "type": "list"}),
            {"id": "each", "type": "for_each", "settings": {"items": "items"}},
            body,
            output_step("results"),
        ],
        [
            ("input", "each"),
            ("each", "Each item", body["id"]),
            ("each", "When done", "output"),
            *extra_conns,
        ],
    )


def test_for_each_body_rules():
    assert not {
        "foreach_bad_body",
        "foreach_body_leads_on",
        "foreach_no_body",
    } & set(codes(_for_each(code_step("work", "return {'y': data['item']}"))))
    leads_on = _for_each(code_step("work"), [("work", "output")])
    assert "foreach_body_leads_on" in codes(leads_on)
    decision = _for_each({"id": "pick", "type": "decision"})
    assert "foreach_bad_body" in codes(decision)
    no_body = make_spec(
        [
            input_step({"name": "items", "type": "list"}),
            {"id": "each", "type": "for_each"},
            output_step(),
        ],
        [("input", "each"), ("each", "When done", "output")],
    )
    assert "foreach_no_body" in codes(no_body)


def test_for_each_items_should_be_a_list():
    spec = make_spec(
        [
            input_step("text"),
            {"id": "each", "type": "for_each", "settings": {"items": "text"}},
            code_step("work"),
            output_step("results"),
        ],
        [("input", "each"), ("each", "Each item", "work"), ("each", "When done", "output")],
    )
    assert "items_not_list" in codes(spec)


def test_sub_flow_problems():
    child = make_spec(
        [
            input_step("text", {"name": "mode", "required": False, "default": "x"}),
            code_step("work"),
            output_step("y"),
        ],
        [("input", "work"), ("work", "output")],
        name="Child",
    )
    broken = make_spec([input_step("q")], [], name="Broken")

    def parent(**settings):
        return make_spec(
            [
                input_step("text"),
                {"id": "sub", "type": "subflow", "settings": settings},
                output_step(),
            ],
            [("input", "sub"), ("sub", "output")],
            name="Parent",
        )

    flows = {"child": child, "broken": broken}
    assert "subflow_no_flow" in codes(parent(), resolve=flows.get)
    assert "subflow_missing" in codes(parent(flow="nope"), resolve=flows.get)
    assert "subflow_has_errors" in codes(parent(flow="broken"), resolve=flows.get)
    assert "subflow_unknown_input" in codes(
        parent(flow="child", inputs={"nope": "{text}"}), resolve=flows.get
    )
    assert "subflow_unknown_output" in codes(
        parent(flow="child", outputs={"mine": "nope"}), resolve=flows.get
    )
    assert "subflow_default_unused" in codes(
        parent(flow="child", share_data=True), resolve=flows.get
    )
    clean = codes(parent(flow="child"), resolve=flows.get)
    assert not [c for c in clean if c.startswith("subflow_")]


def test_a_flow_that_runs_itself_is_refused():
    a = make_spec(
        [
            input_step("x"),
            {"id": "sub", "type": "subflow", "settings": {"flow": "b"}},
            output_step(),
        ],
        [("input", "sub"), ("sub", "output")],
        name="A",
    )
    b = make_spec(
        [
            input_step("x"),
            {"id": "sub", "type": "subflow", "settings": {"flow": "a"}},
            output_step(),
        ],
        [("input", "sub"), ("sub", "output")],
        name="B",
    )
    flows = {"a": a, "b": b}
    found = codes(a, resolve=flows.get, flow_id="a")
    assert "subflow_has_errors" in found  # b can't run a again


def test_jump_checks():
    def jump(**settings):
        return make_spec(
            [input_step("x"), {"id": "go", "type": "jump", "settings": settings}, output_step("x")],
            [("input", "go"), ("go", "output")],
        )

    assert "jump_empty" in codes(jump())
    assert "jump_duplicate_field" in codes(jump(updates=[{"field": "a"}, {"field": "a"}]))
    assert "bad_expression" in codes(jump(updates=[{"field": "a", "expression": "import os"}]))
    assert "missing_field" in codes(jump(updates=[{"field": "a", "value": "{nope}"}]))
    clean = codes(jump(updates=[{"field": "a", "value": "{x}!"}]))
    assert not {"jump_empty", "missing_field", "decision_connection_no_exit"} & set(clean)


def test_exit_label_on_a_step_without_exits_is_flagged():
    spec = make_spec(
        [input_step("x"), code_step("work"), output_step("y")],
        [("input", "work"), ("work", "Yes", "output")],
    )
    assert "exit_on_plain_step" in codes(spec)
