"""The Test Sets API (Phase 5.1, docs/testing.md): read a flow's Test Set, save its cases, and
run them.

A flow's Test Set is the file ``<flow id>.tests.yaml`` next to the flow, so it is kept in git
with it. Running happens here, in the API server, with this workspace's secrets, MCP servers,
sandbox and network policy, and the results come back in the response; Test Runs that are
kept (5.2) go through the job queue.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .. import current_workspace as context
from ..integrations.mcp import connections as mcp_connections
from ..spec import FlowSpec, SpecError, parse_spec
from ..testsets import (
    _substitute,
    case_problems,
    read_test_set,
    run_case,
    write_test_set,
)
from .audit import note
from .auth import require
from .gateway import policy_for
from .integrations_api import mcp_setting
from .network import policy as network_policy
from .spaces import space_of
from .store import FlowNotFound
from .worker import sandbox_settings

VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class TestSetBody(BaseModel):
    cases: list[dict[str, Any]] = Field(default_factory=list)
    stand_in: bool | None = None
    judge_model: str | None = Field(default=None, max_length=200)


class TestRunBody(BaseModel):
    # Only these cases (by name); all of them when left out.
    cases: list[str] | None = None
    # Run with the stand-in AI (True), real models (False), or as the Test Set says (None).
    stand_in: bool | None = None
    # ${name} values in the cases (a test server's address, say).
    variables: dict[str, str] = Field(default_factory=dict)
    # The flow as it is on the canvas, not as saved (an Editor's: it can run any code).
    spec: dict[str, Any] | None = None


def _variables(value: Any) -> list[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            found.update(VARIABLE.findall(node))
        elif isinstance(node, dict):
            for item in node.values():
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return sorted(found)


def add_testset_routes(app: FastAPI) -> None:
    def _flow(request: Request, flow_id: str) -> FlowSpec:
        try:
            return space_of(request).flows.get(flow_id)
        except FlowNotFound:
            raise HTTPException(
                404, detail={"message": "That flow doesn't exist (any more)."}
            ) from None
        except SpecError as exc:
            raise HTTPException(
                422,
                detail={"message": "The saved flow file has problems.", "problems": exc.problems},
            ) from exc

    def _file(request: Request, flow_id: str) -> Any:
        return space_of(request).flows.workspace / f"{flow_id}.tests.yaml"

    def _read(request: Request, flow_id: str) -> dict[str, Any]:
        try:
            return read_test_set(_file(request, flow_id), f"{flow_id}.flow.yaml")
        except (ValueError, OSError) as exc:
            raise HTTPException(422, detail={"message": str(exc)}) from exc

    @app.get("/api/flows/{flow_id}/tests")
    def get_tests(flow_id: str, request: Request) -> dict[str, Any]:
        """The flow's Test Set: its cases, and the ${name} values they need."""
        _flow(request, flow_id)
        data = _read(request, flow_id)
        return {
            "file": f"{flow_id}.tests.yaml",
            "exists": _file(request, flow_id).exists(),
            "flow": data.get("flow"),
            "stand_in": bool(data.get("stand_in")),
            "judge_model": data.get("judge_model"),
            "cases": data["cases"],
            "variables": _variables(data["cases"]),
        }

    @app.put("/api/flows/{flow_id}/tests")
    def save_tests(flow_id: str, body: TestSetBody, request: Request) -> dict[str, Any]:
        """Save the cases (they replace the file's); what isn't a case is kept as it was."""
        _flow(request, flow_id)
        if problems := case_problems(body.cases):
            raise HTTPException(
                422,
                detail={"message": "Some cases can't be saved yet.", "problems": problems},
            )
        data = _read(request, flow_id)
        data["cases"] = body.cases
        if body.stand_in is not None:
            if body.stand_in:
                data["stand_in"] = True
            else:
                data.pop("stand_in", None)
        if "judge_model" in body.model_fields_set:
            if body.judge_model:
                data["judge_model"] = body.judge_model
            else:
                data.pop("judge_model", None)
        write_test_set(_file(request, flow_id), data)
        note(request, flow_id, cases=len(body.cases))
        return {"saved": True, "cases": len(body.cases)}

    @app.post("/api/flows/{flow_id}/tests/run")
    async def run_tests(flow_id: str, body: TestRunBody, request: Request) -> dict[str, Any]:
        """Run the cases now and say how each did: passed, failed (and why) or skipped."""
        space = space_of(request)
        if body.spec is not None:
            require(request, "editor")
            try:
                spec = parse_spec(body.spec)
            except SpecError as exc:
                raise HTTPException(
                    422, detail={"message": "The flow isn't valid.", "problems": exc.problems}
                ) from exc
        else:
            spec = _flow(request, flow_id)
        data = _read(request, flow_id)
        cases = data["cases"]
        if body.cases is not None:
            wanted = set(body.cases)
            cases = [c for c in cases if c.get("name") in wanted]
            missing = wanted - {c.get("name") for c in cases}
            if missing:
                raise HTTPException(
                    404,
                    detail={"message": f"No case called {', '.join(sorted(missing))} here."},
                )
        cases = _substitute(cases, body.variables)
        if left := _variables(cases):
            raise HTTPException(
                422,
                detail={
                    "message": f"Give a value for {', '.join(left)} (used in the cases) to run "
                    "them.",
                    "variables": left,
                },
            )
        stand_in = bool(data.get("stand_in")) if body.stand_in is None else body.stand_in
        db = app.state.hub.db

        def resolve(other: str) -> FlowSpec | None:
            try:
                return space.flows.get(other)
            except (FlowNotFound, SpecError):
                return None

        options = {
            "mcp": mcp_connections(await mcp_setting(db, space.setting("mcp"))),
            "egress": await network_policy(app),
            "resolve": resolve,
            "flow_id": flow_id,
            "redact": space.vault.values() if space.vault is not None else [],
            "gateway": await policy_for(
                app.state.hub,
                kind="test_set",
                flow_id=flow_id,
                flow_name=spec.name,
                by=context.person.get(),
            ),
        }
        sandbox = sandbox_settings(await db.get_setting("sandbox", {}))
        results = [
            await run_case(
                spec, case, stand_in, sandbox, judge_model=data.get("judge_model"), options=options
            )
            for case in cases
        ]
        note(request, flow_id, cases=len(results), passed=sum(r.passed for r in results))
        return {
            "results": [r.to_dict() for r in results],
            "passed": sum(r.passed for r in results),
            "total": len(results),
            "stand_in": stand_in,
        }
