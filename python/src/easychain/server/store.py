"""Where flows and runs live.

Phase 1 keeps flows as ``*.flow.yaml`` files in a workspace folder (easy to put
in git) and recent runs in memory. Phase 2 moves runs, threads and Save Points
to Postgres.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ..spec import FlowSpec, SpecError, dumps_spec, load_spec

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "flow"


class FlowNotFound(KeyError):
    pass


class FlowStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, flow_id: str) -> Path:
        if not ID_RE.match(flow_id):
            raise FlowNotFound(flow_id)
        return self.workspace / f"{flow_id}.flow.yaml"

    def list(self) -> list[dict[str, Any]]:
        flows = []
        for path in sorted(
            self.workspace.glob("*.flow.yaml"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            flow_id = path.name.removesuffix(".flow.yaml")
            entry: dict[str, Any] = {"id": flow_id, "updated": path.stat().st_mtime}
            try:
                spec = load_spec(path)
                entry.update(name=spec.name, description=spec.description, steps=len(spec.steps))
            except SpecError as exc:
                entry.update(name=flow_id, description="", steps=0, problem=exc.problems[0])
            flows.append(entry)
        return flows

    def get(self, flow_id: str) -> FlowSpec:
        path = self._path(flow_id)
        if not path.exists():
            raise FlowNotFound(flow_id)
        return load_spec(path)

    def save(self, flow_id: str, spec: FlowSpec) -> None:
        path = self._path(flow_id)
        with self._lock:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(dumps_spec(spec), encoding="utf-8")
            tmp.replace(path)

    def create(self, spec: FlowSpec) -> str:
        base = slugify(spec.name)
        with self._lock:
            flow_id, n = base, 2
            while (self.workspace / f"{flow_id}.flow.yaml").exists():
                flow_id = f"{base}-{n}"
                n += 1
            (self.workspace / f"{flow_id}.flow.yaml").write_text(dumps_spec(spec), encoding="utf-8")
        return flow_id

    def delete(self, flow_id: str) -> None:
        path = self._path(flow_id)
        if not path.exists():
            raise FlowNotFound(flow_id)
        path.unlink()

    def yaml(self, flow_id: str) -> str:
        path = self._path(flow_id)
        if not path.exists():
            raise FlowNotFound(flow_id)
        return path.read_text(encoding="utf-8")


class RunStore:
    """Recent runs with their full event list (most recent last)."""

    def __init__(self, limit: int = 200):
        self.limit = limit
        self._runs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def start(
        self, run_id: str, flow_id: str | None, flow_name: str, inputs: dict[str, Any]
    ) -> None:
        with self._lock:
            self._runs[run_id] = {
                "run_id": run_id,
                "flow_id": flow_id,
                "flow": flow_name,
                "inputs": inputs,
                "started": time.time(),
                "status": "running",
                "events": [],
            }
            while len(self._runs) > self.limit:
                self._runs.popitem(last=False)

    def add(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            if event["type"] != "token":
                run["events"].append(event)
            if event["type"] == "run_finished":
                run["status"] = event["status"]
                run["duration_ms"] = event.get("duration_ms")
                run["cost"] = event.get("cost")
                run["usage"] = event.get("usage")
                run["thread_id"] = event.get("thread_id")

    def list(self, flow_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            runs = [r for r in self._runs.values() if flow_id is None or r["flow_id"] == flow_id]
            return [{k: v for k, v in r.items() if k != "events"} for r in reversed(runs)]

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return dict(run) if run else None
