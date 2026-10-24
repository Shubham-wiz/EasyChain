"""``easychain eval``, the CI gate (Phase 5.3, evaluate.py, docs/ci.md): with no baseline every
case must pass; with one, only a regression fails the gate (a known failure doesn't), and so
does a run that got too slow or too dear; the budget stops starting cases; the report and the
baseline file say what happened."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from easychain.cli import main
from easychain.evaluate import gate, load_baseline, markdown, run_evaluation, save_baseline
from easychain.spec import dumps_spec, parse_spec

from .test_checks import ECHO


def _write(folder: Path, body: str = "data['text'].upper()", cases: list | None = None) -> Path:
    spec = {**ECHO, "steps": [ECHO["steps"][0], {**ECHO["steps"][1], "settings": {"code": f"def run(data):\n    return {{'loud': {body}, 'size': len(data['text'])}}\n"}}, ECHO["steps"][2]]}  # fmt: skip
    (folder / "echo.flow.yaml").write_text(dumps_spec(parse_spec(spec)), encoding="utf-8")
    cases = cases or [
        {"name": "Shouts", "inputs": {"text": "hi"}, "expect": {"output.loud": "HI"}},
        {"name": "Long", "inputs": {"text": "hello"}, "expect": {"output.size": {"at_least": 9}}},
    ]
    path = folder / "echo.tests.yaml"
    path.write_text(yaml.safe_dump({"flow": "echo.flow.yaml", "cases": cases}), encoding="utf-8")
    return path


async def test_without_a_baseline_every_case_must_pass(tmp_path: Path):
    path = _write(tmp_path)
    found = gate(await run_evaluation(path), None)
    assert not found.ok
    assert found.problems == ["Long failed: output.size: expected at least 9, got 5"]
    report = markdown(found)
    assert report.startswith("### ❌ echo.tests.yaml\n\n1 of 2 cases passed")
    assert "| Shouts | 1/1 | – |" in report and "**Why it failed:**" in report


async def test_only_a_regression_fails_against_the_baseline(tmp_path: Path):
    path = _write(tmp_path)
    first = await run_evaluation(path, repeat=2)
    save_baseline(tmp_path / "echo.baseline.json", first)
    baseline = load_baseline(tmp_path / "echo.baseline.json")
    assert baseline["format"] == "easychain-test-run/1" and baseline["repeat"] == 2
    again = gate(await run_evaluation(path), baseline)
    assert again.ok, "Long failed before too: a known failure, not a regression"
    assert {r["name"]: r["change"] for r in again.comparison["cases"]} == {
        "Shouts": "same",
        "Long": "same",
    }

    _write(tmp_path, body="data['text']")  # the flow breaks
    broken = gate(await run_evaluation(path), baseline)
    assert broken.problems == ["Regression: Shouts: worse (100% → 0%)"]
    assert "| Shouts | 0/1 | ⬇ worse |" in markdown(broken)

    slow = {**baseline, "summary": {**baseline["summary"], "seconds": 0.000001}}
    too_slow = gate(await run_evaluation(path), slow, max_slower=50)
    assert any("longer than the baseline" in p for p in too_slow.problems)
    with pytest.raises(ValueError, match="isn't an Easy Chain baseline"):
        (tmp_path / "bad.json").write_text("{}", encoding="utf-8")
        load_baseline(tmp_path / "bad.json")


async def test_the_budget_stops_starting_cases(tmp_path: Path):
    path = _write(tmp_path)
    spent = gate(await run_evaluation(path, budget=0), None)
    assert spent.not_run == ["Shouts", "Long"] and spent.cases == []
    assert spent.problems == ["The budget was spent before 2 case(s) ran: Shouts, Long."]
    assert "| Shouts | not run (budget) |" in markdown(spent)


def test_the_command_line_gate(tmp_path: Path, capsys: pytest.CaptureFixture):
    path = _write(tmp_path, cases=[{"name": "Shouts", "inputs": {"text": "hi"}, "expect": {"output.loud": "HI"}}])  # fmt: skip
    summary = tmp_path / "summary.md"
    assert main(["eval", str(path), "--save-baseline", "--summary", str(summary)]) == 0
    assert (tmp_path / "echo.baseline.json").exists(), "next to the Test Set"
    assert "### ✅ echo.tests.yaml" in summary.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "✓ Shouts" in out and "Saved the baseline" in out

    _write(tmp_path, body="data['text']", cases=[{"name": "Shouts", "inputs": {"text": "hi"}, "expect": {"output.loud": "HI"}}])  # fmt: skip
    results = tmp_path / "results.json"
    assert main(["eval", str(path), "--summary", str(summary), "--json", str(results)]) == 1
    out = capsys.readouterr().out
    assert "Regression: Shouts: worse (100% → 0%)" in out
    report = summary.read_text(encoding="utf-8")
    assert report.count("### ") == 2, "each run appends its section"
    assert json.loads(results.read_text(encoding="utf-8"))["ok"] is False
