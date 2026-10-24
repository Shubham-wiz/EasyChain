"""Kept Test Runs and baselines (Phase 5.2, docs/testing.md).

A Test Run runs a flow's Test Set against one saved version of the flow, on a worker (a
``test`` job), and keeps every case's results. One Test Run per flow can be its **baseline**;
every other run is compared with it, case by case (testruns.py).

The cases are copied into the Test Run when it starts, so later edits to the Test Set don't
change what a kept run says it ran.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .. import current_workspace
from ..spec import SpecError
from ..testruns import compare
from ..testsets import _substitute, read_test_set
from .audit import note
from .auth import principal_of
from .environments_api import known
from .spaces import space_of
from .store import FlowNotFound
from .testsets_api import _variables

MAX_REPEAT = 10


class TestRunStart(BaseModel):
    # Run each case this many times (real models answer differently; this shows flaky cases).
    repeat: int = Field(default=1, ge=1, le=MAX_REPEAT)
    stand_in: bool | None = None
    cases: list[str] | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    # Run in an environment: its secrets, model and Knowledge Base swaps (docs/publish.md).
    environment: str | None = None


def public(row: dict[str, Any]) -> dict[str, Any]:
    """A Test Run as the API shows it (without its cases' full results)."""
    cases = row.get("cases") or []
    return {
        "id": row["id"],
        "flow_id": row["flow_id"],
        "version_id": row["version_id"],
        "status": row["status"],
        "stand_in": bool(row["stand_in"]),
        "repeat": row["repeat"],
        "environment": row.get("environment"),
        "by": row.get("created_by"),
        "baseline": bool(row["baseline"]),
        "created_at": row["created_at"],
        "finished_at": row.get("finished_at"),
        "error": row.get("error"),
        "progress": {"done": row.get("done") or 0, "total": len(cases) * row["repeat"]},
        "summary": row.get("summary"),
    }


def add_testrun_routes(app: FastAPI) -> None:
    def hub() -> Any:
        return app.state.hub

    async def _mine(run_id: str) -> dict[str, Any]:
        row = await hub().db.get_test_run(run_id)
        if row is None or row["workspace_id"] != current_workspace.current():
            raise HTTPException(404, detail={"message": "That Test Run doesn't exist."})
        return row

    @app.post("/api/flows/{flow_id}/test-runs", status_code=202)
    async def start_test_run(flow_id: str, body: TestRunStart, request: Request) -> dict[str, Any]:
        """Run the saved flow's Test Set on a worker and keep the results."""
        space = space_of(request)
        try:
            spec = space.flows.get(flow_id)
        except FlowNotFound:
            raise HTTPException(
                404, detail={"message": "That flow doesn't exist (any more)."}
            ) from None
        except SpecError as exc:
            raise HTTPException(
                422,
                detail={"message": "The saved flow file has problems.", "problems": exc.problems},
            ) from exc
        try:
            data = read_test_set(
                space.flows.workspace / f"{flow_id}.tests.yaml", f"{flow_id}.flow.yaml"
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(422, detail={"message": str(exc)}) from exc
        cases = data["cases"]
        if body.cases is not None:
            cases = [c for c in cases if c.get("name") in set(body.cases)]
        if not cases:
            raise HTTPException(
                422, detail={"message": "There are no cases to run. Add some first."}
            )
        cases = _substitute(cases, body.variables)
        if left := _variables(cases):
            raise HTTPException(
                422,
                detail={
                    "message": f"Give a value for {', '.join(left)} (used in the cases) to run them.",
                    "variables": left,
                },
            )
        stand_in = bool(data.get("stand_in")) if body.stand_in is None else body.stand_in
        version_id = await hub().db.save_version(flow_id, hub().bundle(spec), note="test run")
        principal = principal_of(request)
        by = principal.label if principal is not None and principal.user_id else None
        run_id = await hub().db.create_test_run(
            flow_id,
            version_id,
            cases,
            stand_in=stand_in,
            repeat=body.repeat,
            judge_model=data.get("judge_model"),
            created_by=by,
            environment=known(body.environment) if body.environment else None,
        )
        hub().bus.job_added()
        note(request, flow_id, test_run=run_id, repeat=body.repeat)
        return {"id": run_id, "status": "queued", "version_id": version_id}

    @app.get("/api/flows/{flow_id}/test-runs")
    async def list_test_runs(flow_id: str, limit: int = 20) -> dict[str, Any]:
        rows = await hub().db.list_test_runs(flow_id, limit=max(1, min(limit, 100)))
        return {"runs": [public(r) for r in rows]}

    @app.get("/api/test-runs/{run_id}")
    async def get_test_run(run_id: str) -> dict[str, Any]:
        """A Test Run with each case's results, compared with the flow's baseline."""
        row = await _mine(run_id)
        baseline = await hub().db.baseline_of(row["flow_id"])
        comparison = None
        if baseline is not None and baseline["id"] != row["id"] and row["status"] == "done":
            comparison = compare(row.get("results") or [], baseline.get("results") or [])
        return {
            **public(row),
            "cases": row.get("results") or [],
            "baseline_id": baseline["id"] if baseline else None,
            "comparison": comparison,
        }

    @app.post("/api/test-runs/{run_id}/baseline")
    async def make_baseline(run_id: str, request: Request) -> dict[str, Any]:
        """Make this Test Run the one its flow's other runs are compared with."""
        row = await _mine(run_id)
        if row["status"] != "done":
            raise HTTPException(
                409, detail={"message": "Only a Test Run that has finished can be the baseline."}
            )
        await hub().db.set_baseline(run_id, row["flow_id"])
        note(request, row["flow_id"], test_run=run_id)
        return {"baseline": run_id}
