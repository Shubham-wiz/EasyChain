"""Test Runs and baselines (Phase 5.2, testruns.py, server/testruns_api.py): repeated cases
give a pass rate and show flakiness; a run is compared case by case with the baseline, with the
change in time, tokens and cost."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from easychain.server.app import create_app
from easychain.testruns import case_summary, compare, describe_change, summary

from .test_api_phase2 import code
from .test_checks import ECHO


def result(passed: bool, seconds: float = 1.0, cost: float | None = 0.001, failure: str = "x"):
    return {
        "passed": passed,
        "failures": [] if passed else [failure],
        "skipped": [],
        "metrics": {"seconds": seconds, "tokens": 100, "cost": cost, "tools": []},
    }


def test_repeats_give_a_pass_rate_and_show_flaky_cases():
    steady = case_summary("Steady", [result(True), result(True)])
    flaky = case_summary(
        "Flaky", [result(True), result(False, failure="a"), result(False, failure="a")]
    )
    unpriced = case_summary("Unpriced", [result(True, cost=None), result(True)])
    assert (steady["pass_rate"], steady["flaky"]) == (1.0, False)
    assert (flaky["pass_rate"], flaky["flaky"], flaky["failures"]) == (0.3333, True, ["a"])
    assert unpriced["cost"] is None, "an average of known and unknown costs isn't a cost"
    whole = summary([steady, flaky, unpriced])
    assert {k: whole[k] for k in ("total", "passed", "failed", "flaky", "cost")} == {
        "total": 3,
        "passed": 2,
        "failed": 1,
        "flaky": 1,
        "cost": None,
    }


def test_each_case_is_compared_with_the_baseline():
    baseline = [
        case_summary("Kept", [result(True, seconds=1.0)]),
        case_summary("Broke", [result(True)]),
        case_summary("Fixed", [result(False)]),
        case_summary("Dropped", [result(True)]),
    ]
    current = [
        case_summary("Kept", [result(True, seconds=2.5, cost=0.003)]),
        case_summary("Broke", [result(True), result(False)]),
        case_summary("Fixed", [result(True)]),
        case_summary("Added", [result(True)]),
    ]
    found = compare(current, baseline)
    changes = {row["name"]: row["change"] for row in found["cases"]}
    assert changes == {
        "Kept": "same",
        "Broke": "worse",
        "Fixed": "better",
        "Added": "new",
        "Dropped": "gone",
    }
    assert found["regressions"] == ["Broke"]
    assert (found["better"], found["worse"], found["same"], found["new"], found["gone"]) == (
        1,
        1,
        1,
        1,
        1,
    )
    kept = next(r for r in found["cases"] if r["name"] == "Kept")
    assert (kept["seconds"], kept["cost"]) == (1.5, 0.002)
    assert describe_change(kept) == "Kept: same (100% → 100%), +1.5 s, +0.0020 $"
    broke = next(r for r in found["cases"] if r["name"] == "Broke")
    assert describe_change(broke) == "Broke: worse (100% → 50%)"


# ── kept Test Runs through the API, on the inline worker ────────────────────


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        home=tmp_path / "home",
        workspace=tmp_path / "flows",
        static_dir=tmp_path / "no-web",
        worker_options={"poll": 0.05, "schedules": False},
    )
    with TestClient(app) as test_client:
        yield test_client


def _finished(client: TestClient, run_id: str, headers: dict | None = None) -> dict:
    for _ in range(400):
        found = client.get(f"/api/test-runs/{run_id}", headers=headers or {}).json()
        if found["status"] in ("done", "error"):
            return found
        time.sleep(0.05)
    raise AssertionError("the Test Run didn't finish")


def test_kept_runs_are_compared_with_the_baseline(client: TestClient):
    flow_id = client.post("/api/flows", json={"spec": ECHO}).json()["id"]
    assert client.post(f"/api/flows/{flow_id}/test-runs", json={}).status_code == 422, "no cases"
    cases = [
        {"name": "Shouts", "inputs": {"text": "hi"}, "expect": {"output.loud": "HI"}},
        {"name": "Counts", "inputs": {"text": "four"}, "expect": {"output.size": 4}},
    ]
    client.put(f"/api/flows/{flow_id}/tests", json={"cases": cases})
    started = client.post(f"/api/flows/{flow_id}/test-runs", json={"repeat": 2})
    assert started.status_code == 202, started.text
    first = _finished(client, started.json()["id"])
    assert first["status"] == "done" and first["progress"] == {"done": 4, "total": 4}
    assert first["summary"]["passed"] == 2 and first["comparison"] is None
    assert [(c["name"], c["runs"], c["pass_rate"]) for c in first["cases"]] == [
        ("Shouts", 2, 1.0),
        ("Counts", 2, 1.0),
    ]
    assert client.post(f"/api/test-runs/{first['id']}/baseline").json() == {"baseline": first["id"]}

    # The flow changes, and one case gets worse; editing the Test Set doesn't change the old run.
    broken = {**ECHO, "steps": [ECHO["steps"][0], code("shout", "    return {'loud': data['text'], 'size': len(data['text'])}"), ECHO["steps"][2]]}  # fmt: skip
    client.put(f"/api/flows/{flow_id}", json=broken)
    client.put(f"/api/flows/{flow_id}/tests", json={"cases": [*cases, {"name": "New", "inputs": {"text": "x"}}]})  # fmt: skip
    second = _finished(client, client.post(f"/api/flows/{flow_id}/test-runs", json={}).json()["id"])
    assert second["version_id"] != first["version_id"]
    comparison = second["comparison"]
    assert {r["name"]: r["change"] for r in comparison["cases"]} == {
        "Shouts": "worse",
        "Counts": "same",
        "New": "new",
    }
    assert comparison["regressions"] == ["Shouts"]
    assert second["cases"][0]["failures"] == ["output.loud: expected 'HI', got 'hi'"]
    assert len(_finished(client, first["id"])["cases"]) == 2, "the old run keeps its cases"

    listed = client.get(f"/api/flows/{flow_id}/test-runs").json()["runs"]
    assert [r["id"] for r in listed] == [second["id"], first["id"]]
    assert [r["baseline"] for r in listed] == [False, True]
    client.post(f"/api/test-runs/{second['id']}/baseline")
    listed = client.get(f"/api/flows/{flow_id}/test-runs").json()["runs"]
    assert [r["baseline"] for r in listed] == [True, False], "one baseline per flow"
    audit = {e["action"] for e in client.get("/api/audit").json()["entries"]}
    assert {"flow.test_run_kept", "flow.baseline"} <= audit

    # Another workspace doesn't see them.
    client.post("/api/workspaces", json={"name": "Other", "id": "other"})
    other = {"X-EasyChain-Workspace": "other"}
    assert client.get(f"/api/test-runs/{first['id']}", headers=other).status_code == 404
    assert client.get(f"/api/flows/{flow_id}/test-runs", headers=other).json() == {"runs": []}
