"""Where flows and runs live.

Flows are ``*.flow.yaml`` files in a workspace folder (easy to put in git). Runs,
their events, flow versions and Save Points are in the database (``db.py``).
"""

from __future__ import annotations

import re
import threading
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
