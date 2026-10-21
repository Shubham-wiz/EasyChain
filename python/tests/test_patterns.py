"""Multi-agent patterns (Phase 4.5): supervisor, handoffs and plan-and-execute, each built from
ordinary steps. Their templates pass their Test Sets (test_templates.py); here: they use no
special step types, an AI Decision sees Flow Data in its guidance (a supervisor sees the notes),
and each exported pattern runs on its own."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from easychain.compiler import compile_flow
from easychain.compiler.validate import validate
from easychain.export import export_files
from easychain.templates import TEMPLATES, load_template
from easychain.testing.fake_openai import FakeOpenAI

from .conftest import input_step, make_spec, output_step

PATTERNS = [t["id"] for t in TEMPLATES if t.get("pattern")]
ORDINARY = {"input", "output", "instructions", "ai_model", "agent", "code", "decision", "jump"}
ORDINARY |= {"for_each", "http_request"}


def test_the_three_patterns_are_templates_made_of_ordinary_steps():
    assert {t["pattern"] for t in TEMPLATES if t.get("pattern")} == {
        "supervisor",
        "handoffs",
        "plan-and-execute",
    }
    for template_id in PATTERNS:
        spec = load_template(template_id)
        assert {s.type for s in spec.steps} <= ORDINARY, template_id
        assert compile_flow(spec).issues == []


def test_an_ai_decision_sees_flow_data_in_its_guidance():
    def flow(*fields: str):
        return make_spec(
            [
                input_step(*fields),
                {
                    "id": "pick",
                    "type": "decision",
                    "settings": {
                        "mode": "ai",
                        "input": "request",
                        "instructions": "Notes so far: {notes}. Reader: {reader}.",
                        "exits": [{"label": "More"}],
                        "otherwise": "Done",
                    },
                },
                output_step(),
            ],
            [("input", "pick"), ("pick", "More", "output"), ("pick", "Done", "output")],
        )

    missing = [
        i.message
        for i in validate(flow("request"))
        if i.code == "missing_field" and i.setting == "instructions"
    ]
    assert len(missing) == 2 and "`notes`" in missing[0] and "`reader`" in missing[1]
    source = compile_flow(flow("request", "notes", "reader")).source
    assert '("system", fill(PICK_GUIDANCE, data))' in source


def run_export(
    template_id: str, fake: FakeOpenAI, tmp: Path, args: list[str], stdin: str | None = None
) -> str:
    out = tmp / template_id
    out.mkdir()
    for name, content in export_files(load_template(template_id)).items():
        (out / name).write_text(content, encoding="utf-8")
    script = next(p.name for p in out.glob("*.py"))
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("OPENAI", "ANTHROPIC", "EASYCHAIN"))
    }
    env.update(
        OPENAI_API_KEY="sk-test-patterns", OPENAI_BASE_URL=fake.base_url, PYTHONIOENCODING="utf-8"
    )
    hide = (
        "import sys; sys.modules['easychain'] = None; import runpy; "
        "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
    )
    done = subprocess.run(
        [sys.executable, "-c", hide, script, *args],
        cwd=out,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.fixture
def fake():
    with FakeOpenAI() as server:
        yield server


def test_the_exported_supervisor_runs_on_its_own(fake: FakeOpenAI, tmp_path: Path):
    httpx.post(
        f"{fake.url}/__script",
        json=[
            "Billing",
            {"call": "find_invoices", "args": {}},
            "Invoice 1042 was paid twice; refunded the second payment.",
            "Finish",
            "We refunded the second payment for invoice 1042.",
        ],
    )
    result = json.loads(
        run_export("supervisor-team", fake, tmp_path, [json.dumps({"request": "Charged twice"})])
    )
    assert "1042" in result["billing_note"]
    assert "refunded" in result["answer"]


def test_the_exported_handoffs_chat_runs_on_its_own(fake: FakeOpenAI, tmp_path: Path):
    httpx.post(
        f"{fake.url}/__script",
        json=[
            "Passing you to the flights desk.",
            "Flights",
            {"call": "find_flights", "args": {"destination": "Rome"}},
            "EZ145 leaves at 17:40 for 64 euros.",
            "Stay",
            {"call": "find_flights", "args": {"destination": "Rome"}},
            "EZ101 leaves at 08:10.",
            "Stay",
        ],
    )
    out = run_export(
        "handoff-chat", fake, tmp_path, [], stdin="A flight to Rome\nAn earlier one?\n"
    )
    assert "EZ145" in out and "EZ101" in out
    assert "Passing you" not in out.split("EZ145")[1], "the second turn went straight to flights"


def test_the_exported_plan_and_execute_runs_on_its_own(fake: FakeOpenAI, tmp_path: Path):
    httpx.post(
        f"{fake.url}/__script",
        json=[
            {"answer": {"steps": ["Flight to Rome", "Hotel in Rome"]}},
            {"call": "find_prices", "args": {"query": "flight to Rome"}},
            "89 euros.",
            {"call": "find_prices", "args": {"query": "hotel in Rome"}},
            "95 euros a night.",
            "One night in Rome with the flight is 184 euros.",
        ],
    )
    result = json.loads(
        run_export("plan-and-execute", fake, tmp_path, [json.dumps({"goal": "Rome"})])
    )
    assert result["steps"] == ["Flight to Rome", "Hotel in Rome"]
    assert len(result["results"]) == 2 and "89" in result["results"][0]
    assert "184" in result["answer"]
