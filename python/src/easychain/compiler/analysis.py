"""Static analysis of a flow: graph shape, Flow Data fields, and what each step reads and writes.

The validator, the compiler and the web app's Flow Data panel all use this, so
there is one answer to "which fields exist and who sets them".
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..spec.models import Connection, FlowSpec

EACH_ITEM = "Each item"
WHEN_DONE = "When done"

if TYPE_CHECKING:
    from ..steps.base import StepHandler


@dataclass
class FieldInfo:
    name: str
    type: str = "text"
    update: str = "replace"
    description: str = ""
    declared: bool = False
    is_input: bool = False
    is_output: bool = False
    written_by: list[str] = field(default_factory=list)
    read_by: list[str] = field(default_factory=list)
    # Bookkeeping fields Easy Chain adds itself (loop counters, For Each results).
    private: bool = False
    # A helper reducer for update rules Easy Chain manages (e.g. "collect_items").
    reducer: str | None = None
    # Loop counters are reset to their start value by every new run.
    reset: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "update": self.update,
            "description": self.description,
            "declared": self.declared,
            "role": "input" if self.is_input else ("output" if self.is_output else "internal"),
            "is_input": self.is_input,
            "is_output": self.is_output,
            "written_by": self.written_by,
            "read_by": self.read_by,
            "private": self.private,
        }


Resolver = Callable[[str], "FlowSpec | None"]


class FlowAnalysis:
    def __init__(
        self,
        spec: FlowSpec,
        *,
        resolve: Resolver | None = None,
        flow_id: str | None = None,
        parents: tuple[str, ...] = (),
    ):
        from ..steps import handler_for

        self.spec = spec
        self.resolve = resolve
        self.flow_id = flow_id
        self.parents = parents
        self._children: dict[str, FlowAnalysis | None] = {}
        self.steps = spec.step_map()
        self.handlers: dict[str, StepHandler] = {
            sid: handler_for(s.type) for sid, s in self.steps.items()
        }
        self.outgoing: dict[str, list[Connection]] = {sid: [] for sid in self.steps}
        self.incoming: dict[str, list[Connection]] = {sid: [] for sid in self.steps}
        self.bad_connections: list[Connection] = []
        for conn in spec.connections:
            if conn.source in self.steps and conn.target in self.steps:
                self.outgoing[conn.source].append(conn)
                self.incoming[conn.target].append(conn)
            else:
                self.bad_connections.append(conn)

        self.index = {step.id: i for i, step in enumerate(spec.steps)}
        self.input_steps = [s for s in spec.steps if s.type == "input"]
        self.output_steps = [s for s in spec.steps if s.type == "output"]
        self.input_step = self.input_steps[0] if self.input_steps else None
        self.chat = bool(self.input_step and self.input_step.settings.mode == "chat")

        self.depth = self._bfs_depth()
        self.reachable = set(self.depth)
        # Reachable steps first (in BFS order), then the rest in file order.
        self.order = sorted(
            self.steps, key=lambda sid: (self.depth.get(sid, 10**9), self._index(sid))
        )
        self.ancestors = {sid: self._ancestors(sid) for sid in self.steps}
        self.in_cycle = {sid for sid in self.steps if sid in self.ancestors[sid]}
        # For Each: the step each one runs per item ("Each item" exit) -> the For Each step.
        self.foreach_body: dict[str, str] = {}
        for sid, step in self.steps.items():
            if step.type == "for_each":
                for conn in self.outgoing[sid]:
                    if conn.exit == EACH_ITEM and conn.target not in self.foreach_body:
                        self.foreach_body[conn.target] = sid

        # Steps an Agent uses as tools -> the first Agent that uses them. They don't run as
        # part of the flow, so what they save doesn't become Flow Data.
        self.tool_of: dict[str, str] = {}
        for sid, step in self.steps.items():
            if step.type == "agent":
                for tool_id in step.settings.tools:
                    if tool_id in self.steps and tool_id != sid:
                        self.tool_of.setdefault(tool_id, sid)

        self.fields: dict[str, FieldInfo] = {}
        self.field_conflicts: list[tuple[str, str, str]] = []
        self.reads: dict[str, set[str]] = {}
        self.writes: dict[str, dict[str, str]] = {}
        self._infer_fields()

    # ── graph ────────────────────────────────────────────────────────────────

    def _index(self, sid: str) -> int:
        return self.index[sid]

    def _bfs_depth(self) -> dict[str, int]:
        depth: dict[str, int] = {}
        queue: deque[str] = deque()
        for step in self.input_steps:
            depth[step.id] = 0
            queue.append(step.id)
        while queue:
            sid = queue.popleft()
            for conn in self.outgoing[sid]:
                if conn.target not in depth:
                    depth[conn.target] = depth[sid] + 1
                    queue.append(conn.target)
        return depth

    def _ancestors(self, sid: str) -> set[str]:
        """Steps from which ``sid`` can be reached (includes ``sid`` only if it is in a loop)."""
        seen: set[str] = set()
        stack = [c.source for c in self.incoming[sid]]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(c.source for c in self.incoming[cur])
        return seen

    def predecessors(self, sid: str) -> list[str]:
        """Direct predecessors, the ones closest to Input first (so loop back-edges come last)."""
        preds = list(dict.fromkeys(c.source for c in self.incoming[sid]))
        return sorted(preds, key=lambda p: (self.depth.get(p, 10**9), self._index(p)))

    def upstream_output(self, sid: str, _seen: set[str] | None = None) -> str | None:
        """The field the previous step saved: the default input for AI Model and AI Decision steps."""
        seen = _seen or set()
        for pred in self.predecessors(sid):
            if pred in seen:
                continue
            seen.add(pred)
            step = self.steps[pred]
            if step.type == "input":
                if self.chat:
                    return "messages"
                fields = step.settings.fields
                return fields[0].name if fields else None
            out = self.handlers[pred].primary_output(step)
            if out:
                return out
            found = self.upstream_output(pred, seen)
            if found:
                return found
        return None

    def available_fields(self, sid: str) -> set[str]:
        """Fields that may have a value when ``sid`` runs.

        A step used as a tool gets what its Agent can see, plus whatever else it reads
        (the agent fills those in when it calls the tool).
        """
        if sid in self.tool_of:
            return self.available_fields(self.tool_of[sid]) | self.reads.get(sid, set())
        names = {f.name for f in self.fields.values() if f.is_input}
        for anc in self.ancestors[sid]:
            names.update(self.writes.get(anc, {}))
        return names

    # ── sub-flows ────────────────────────────────────────────────────────────

    def child(self, flow_id: str) -> FlowAnalysis | None:
        """Analysis of a flow used as a Sub-flow, or None if it can't be found or would loop."""
        if not flow_id or self.resolve is None:
            return None
        if flow_id in self._children:
            return self._children[flow_id]
        chain = (*self.parents, self.flow_id) if self.flow_id else self.parents
        result: FlowAnalysis | None = None
        if flow_id not in chain:
            spec = self.resolve(flow_id)
            if spec is not None:
                result = FlowAnalysis(spec, resolve=self.resolve, flow_id=flow_id, parents=chain)
        self._children[flow_id] = result
        return result

    def includes_itself(self, flow_id: str) -> bool:
        chain = (*self.parents, self.flow_id) if self.flow_id else self.parents
        return flow_id in chain

    # ── Flow Data ────────────────────────────────────────────────────────────

    def _infer_fields(self) -> None:
        for decl in self.spec.data:
            self.fields[decl.name] = FieldInfo(
                decl.name, decl.type, decl.update, decl.description, declared=True
            )
        if self.chat:
            info = self.fields.setdefault(
                "messages",
                FieldInfo("messages", "messages", "append", "The conversation so far."),
            )
            info.is_input = True
        if self.input_step is not None:
            for f in self.input_step.settings.fields:
                info = self.fields.get(f.name)
                if info is None:
                    info = self.fields[f.name] = FieldInfo(f.name, f.type, "replace", f.description)
                info.is_input = True
        for sid in self.order:
            step = self.steps[sid]
            for info in self.handlers[sid].system_fields(step, self):
                self.fields.setdefault(info.name, info).written_by.append(sid)
        for sid in self.order:
            step = self.steps[sid]
            handler = self.handlers[sid]
            if sid in self.tool_of:
                self.writes[sid] = {}
                continue
            writes = handler.writes(step, self)
            self.writes[sid] = writes
            for name, ftype in writes.items():
                info = self.fields.get(name)
                if info is None:
                    update = "replace"
                    self.fields[name] = FieldInfo(name, ftype, update, written_by=[sid])
                    continue
                info.written_by.append(sid)
                if info.declared or ftype == "any" or info.type == ftype:
                    continue
                if info.type == "any" and not info.declared:
                    info.type = ftype
                elif not info.is_input:
                    self.field_conflicts.append((name, info.type, ftype))
                    info.type = "any"
        for sid in self.order:
            step = self.steps[sid]
            reads = self.handlers[sid].reads(step, self)
            self.reads[sid] = reads
            for name in reads:
                if name in self.fields:
                    self.fields[name].read_by.append(sid)
        for out in self.output_steps:
            for name in out.settings.fields:
                if name in self.fields:
                    self.fields[name].is_output = True
        if self.chat and "messages" in self.fields:
            self.fields["messages"].is_output = True

    def tool_args(self, tool_id: str) -> list[str]:
        """What the agent fills in when it calls a tool step: the fields the step reads that
        its Agent can't already see in Flow Data."""
        agent = self.tool_of.get(tool_id)
        if agent is None:
            return []
        known = self.available_fields(agent)
        return sorted(self.reads.get(tool_id, set()) - known)

    def field_type(self, name: str | None) -> str:
        if name and name in self.fields:
            return self.fields[name].type
        return "any"

    def output_fields(self) -> list[str]:
        names: dict[str, None] = {}
        if self.chat:
            names["messages"] = None
        for out in self.output_steps:
            for name in out.settings.fields:
                names.setdefault(name, None)
        return list(names)

    def round_counters(self) -> dict[str, Any]:
        """Loop counters and their start values (every new run resets them)."""
        return {f.name: f.reset for f in self.fields.values() if f.reset is not None}

    def summary(self) -> dict[str, Any]:
        return {
            "chat": self.chat,
            "fields": [f.to_dict() for f in self.fields.values()],
            "foreach_body": self.foreach_body,
            "tool_of": self.tool_of,
            "reads": {k: sorted(v) for k, v in self.reads.items()},
            "writes": self.writes,
            "reachable": sorted(self.reachable),
            "upstream": {sid: self.upstream_output(sid) for sid in self.steps},
            "exits": {
                sid: self.handlers[sid].exits(step)
                for sid, step in self.steps.items()
                if self.handlers[sid].exits(step)
            },
        }
