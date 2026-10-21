"""The Research assistant template (Phase 4's "Done when"): a Deep Agent with two Helpers, skills,
a to-do list and files. Its Test Set runs in test_templates.py; here: the template uses each of
those parts, and the exported project runs on its own, skills included."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from easychain.compiler import compile_flow
from easychain.templates import load_template
from easychain.testing.fake_openai import FakeOpenAI

from .test_patterns import run_export


@pytest.fixture
def fake():
    with FakeOpenAI() as server:
        yield server


def test_the_template_is_a_deep_agent_with_helpers_skills_a_plan_and_files():
    spec = load_template("research-assistant")
    steps = spec.step_map()
    lead = steps["lead"].settings
    assert lead.helpers == ["researcher", "reviewer"]
    assert lead.skills == ["research-report"] and lead.files == "files" and lead.save_todos
    assert steps["researcher"].settings.tools == ["read_page"]
    assert steps["researcher"].settings.skills == ["source-notes"]
    source = compile_flow(spec).source
    assert "create_deep_agent(" in source
    assert "/skills/lead/research-report/SKILL.md" in source
    assert "/skills/researcher/source-notes/SKILL.md" in source


def test_the_exported_research_assistant_runs_on_its_own(fake: FakeOpenAI, tmp_path: Path):
    # No script: the fake server plays the model as the stand-in does. It plans, reads the skill
    # that fits, hands the reading to the researcher (who reads both pages), and writes the
    # report its instructions ask for.
    bees, coffee = f"{fake.url}/pages/bees", f"{fake.url}/pages/coffee"
    request = f"What are honey bees and coffee like?\n{bees}\n{coffee}"
    result = json.loads(
        run_export("research-assistant", fake, tmp_path, [json.dumps({"request": request})])
    )
    report = result["files"]["/report.md"]
    assert report.startswith("# What are honey bees and coffee like?")
    assert "colonies" in report, "the report has what the researcher read"
    assert result["plan"] and result["plan"][0]["status"] == "in_progress"
    assert "/report.md" in result["answer"]
    assert not any(path.startswith("/skills/") for path in result["files"]), (
        "the skills are the agent's to read, not part of the files it hands back"
    )
