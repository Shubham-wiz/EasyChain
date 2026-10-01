// Pure helpers that edit a FlowSpec. Every function returns a new spec and never
// mutates its input, so they are easy to test and play well with undo/redo.

import dagre from "@dagrejs/dagre";
import type { Connection, FlowSpec, Position, Step, StepType, StepTypeInfo } from "./types";

export const NODE_WIDTH = 220;
export const NODE_HEIGHT = 92;
export const DECISION_WIDTH = 240;

const RESERVED_IDS = new Set([
  "data", "graph", "fill", "json", "sys", "os", "re", "httpx", "start", "end", "build_graph",
]);

export function clone<T>(value: T): T {
  return structuredClone(value);
}

export function toIdent(text: string, fallback = "step"): string {
  let id = text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40);
  if (!id || !/^[a-z]/.test(id)) id = `${fallback}${id ? `_${id}` : ""}`;
  return id;
}

/** Names already used by steps or Flow Data fields (they share LangGraph's namespace). */
export function usedNames(spec: FlowSpec, extraFields: string[] = []): Set<string> {
  const names = new Set<string>(RESERVED_IDS);
  for (const step of spec.steps) {
    names.add(step.id);
    const s = step.settings ?? {};
    if (typeof s.save_as === "string") names.add(s.save_as);
    if (step.type === "input") for (const f of s.fields ?? []) names.add(f.name);
    if (step.type === "output") for (const f of s.fields ?? []) names.add(f);
  }
  for (const f of spec.data) names.add(f.name);
  for (const f of extraFields) names.add(f);
  if (spec.steps.some((s) => s.type === "input" && s.settings?.mode === "chat")) names.add("messages");
  return names;
}

export function uniqueName(base: string, taken: Set<string>): string {
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base}_${n}`)) n += 1;
  return `${base}_${n}`;
}

export function createStep(info: StepTypeInfo, spec: FlowSpec, extraFields: string[] = []): Step {
  const taken = usedNames(spec, extraFields);
  const settings = clone(info.defaults) as Record<string, unknown>;
  const name = info.default_name;
  let baseId = toIdent(name);
  if (info.type === "input" || info.type === "output") baseId = info.type;
  const id = uniqueName(baseId, taken);
  return { id, type: info.type, name, description: "", settings };
}

export function getStep(spec: FlowSpec, id: string): Step | undefined {
  return spec.steps.find((s) => s.id === id);
}

export function addStep(spec: FlowSpec, step: Step, position: Position, connectFrom?: { step: string; exit?: string | null }): FlowSpec {
  const next = clone(spec);
  next.steps.push(step);
  next.canvas.steps[step.id] = position;
  if (connectFrom && !canConnect(next, connectFrom.step, step.id, connectFrom.exit ?? null)) {
    next.connections.push({ from: connectFrom.step, to: step.id, exit: connectFrom.exit ?? null });
  }
  return next;
}

export function updateStep(spec: FlowSpec, id: string, patch: Partial<Omit<Step, "id" | "type">>): FlowSpec {
  const next = clone(spec);
  const step = next.steps.find((s) => s.id === id);
  if (step) Object.assign(step, patch);
  return next;
}

export function updateSettings(spec: FlowSpec, id: string, patch: Record<string, unknown>): FlowSpec {
  const next = clone(spec);
  const step = next.steps.find((s) => s.id === id);
  if (!step) return spec;
  step.settings = { ...step.settings, ...patch };
  // Renaming an exit keeps its connection attached.
  if (hasExits(step) && ("exits" in patch || "otherwise" in patch || "options" in patch || "kind" in patch)) {
    const before = exitLabels(getStep(spec, id)!);
    const after = exitLabels(step);
    if (before.length === after.length) {
      for (const conn of next.connections) {
        if (conn.from !== id || conn.exit == null) continue;
        const idx = before.indexOf(conn.exit);
        if (idx !== -1 && after[idx] !== conn.exit) conn.exit = after[idx];
      }
    }
  }
  return next;
}

export const EACH_ITEM = "Each item";
export const WHEN_DONE = "When done";

/** The labelled exits a step leaves by (a step without exits has one plain way out). */
export function exitLabels(step: Step): string[] {
  const s = step.settings ?? {};
  switch (step.type) {
    case "decision":
      return [...((s.exits ?? []) as { label: string }[]).map((e) => e.label), s.otherwise ?? "Otherwise"];
    case "jump":
      return [...((s.exits ?? []) as { label: string }[]).map((e) => e.label), s.otherwise ?? "Next"];
    case "ask_human":
      if (s.kind === "choose") return Array.from(new Set((s.options ?? []) as string[]));
      if (s.kind === "answer") return [];
      return ["Approved", "Rejected"];
    case "for_each":
      return [EACH_ITEM, WHEN_DONE];
    default:
      return [];
  }
}

export function hasExits(step: Step): boolean {
  return exitLabels(step).length > 0;
}

/** Why a connection is not allowed, or null when it is fine. */
export function canConnect(spec: FlowSpec, from: string, to: string, exit: string | null): string | null {
  const source = getStep(spec, from);
  const target = getStep(spec, to);
  if (!source || !target) return "That step no longer exists.";
  if (from === to) return "A step can't connect to itself.";
  if (target.type === "input") return "Nothing can lead into Input; it's where runs start.";
  if (source.type === "output") return "Output is the end of the flow.";
  if (source.type === "input" && target.type === "output") return "Put at least one step between Input and Output.";
  if (hasExits(source) && !exit) return "Drag from one of the step's exits.";
  if (hasExits(source) && spec.connections.some((c) => c.from === from && c.exit === exit))
    return `The exit “${exit}” already leads somewhere. Delete that connection first.`;
  if (source.type === "for_each" && exit === EACH_ITEM) {
    if (target.type === "output" || hasExits(target) || target.type === "for_each")
      return "“Each item” leads to one action, AI or Sub-flow step. To choose per item, use a Sub-flow.";
    if (spec.connections.some((c) => c.to === to)) return "That step already has a way in; the step run per item can't have another.";
  }
  const body = spec.connections.find((c) => c.to === from && c.exit === EACH_ITEM);
  if (body) return "This step runs once per item, so it can't lead on. Connect the For Each's “When done” exit instead.";
  if (spec.connections.some((c) => c.to === to && c.exit === EACH_ITEM))
    return "This step runs once per item for a For Each; nothing else can lead into it.";
  if (spec.connections.some((c) => c.from === from && c.to === to && (c.exit ?? null) === (exit ?? null)))
    return "These steps are already connected.";
  return null;
}

export function connect(spec: FlowSpec, from: string, to: string, exit: string | null = null): FlowSpec {
  if (canConnect(spec, from, to, exit)) return spec;
  const next = clone(spec);
  next.connections.push({ from, to, exit });
  return next;
}

export function sameConnection(a: Connection, b: Connection): boolean {
  return a.from === b.from && a.to === b.to && (a.exit ?? null) === (b.exit ?? null);
}

export function removeConnection(spec: FlowSpec, conn: Connection): FlowSpec {
  const next = clone(spec);
  next.connections = next.connections.filter((c) => !sameConnection(c, conn));
  return next;
}

export function removeSteps(spec: FlowSpec, ids: string[]): FlowSpec {
  const drop = new Set(ids);
  const next = clone(spec);
  next.steps = next.steps.filter((s) => !drop.has(s.id));
  next.connections = next.connections.filter((c) => !drop.has(c.from) && !drop.has(c.to));
  for (const id of ids) delete next.canvas.steps[id];
  return next;
}

export function moveSteps(spec: FlowSpec, positions: Record<string, Position>): FlowSpec {
  const next = clone(spec);
  for (const [id, pos] of Object.entries(positions)) {
    next.canvas.steps[id] = { x: Math.round(pos.x), y: Math.round(pos.y) };
  }
  return next;
}

export function renameStepId(spec: FlowSpec, from: string, to: string): FlowSpec {
  if (from === to || !getStep(spec, from) || getStep(spec, to)) return spec;
  const next = clone(spec);
  for (const step of next.steps) if (step.id === from) step.id = to;
  for (const conn of next.connections) {
    if (conn.from === from) conn.from = to;
    if (conn.to === from) conn.to = to;
  }
  if (next.canvas.steps[from]) {
    next.canvas.steps[to] = next.canvas.steps[from];
    delete next.canvas.steps[from];
  }
  return next;
}

/** Put a new step between a step and everything that currently leads into it. */
export function insertBefore(spec: FlowSpec, beforeId: string, step: Step): FlowSpec {
  const target = spec.canvas.steps[beforeId] ?? { x: 0, y: 0 };
  let next = clone(spec);
  next.steps.push(step);
  next.canvas.steps[step.id] = { x: target.x - NODE_WIDTH - 60, y: target.y };
  for (const conn of next.connections) if (conn.to === beforeId) conn.to = step.id;
  next.connections.push({ from: step.id, to: beforeId, exit: null });
  // Shift the target and everything to its right to make room.
  next = shiftRight(next, target.x, NODE_WIDTH + 60, step.id);
  return next;
}

function shiftRight(spec: FlowSpec, fromX: number, by: number, except: string): FlowSpec {
  for (const [id, pos] of Object.entries(spec.canvas.steps)) {
    if (id !== except && pos.x >= fromX) spec.canvas.steps[id] = { x: pos.x + by, y: pos.y };
  }
  return spec;
}

/** Replace {from} with {to} in a template setting, or a field reference equal to `from`. */
export function renameVariable(spec: FlowSpec, stepId: string, setting: string, from: string, to: string): FlowSpec {
  const step = getStep(spec, stepId);
  if (!step) return spec;
  const value = step.settings[setting];
  if (typeof value === "string") {
    const replaced = value.includes(`{${from}}`) ? value.split(`{${from}}`).join(`{${to}}`) : value === from ? to : value;
    return updateSettings(spec, stepId, { [setting]: replaced });
  }
  if (setting === "headers" && value && typeof value === "object") {
    const headers = Object.fromEntries(
      Object.entries(value as Record<string, string>).map(([k, v]) => [k, v.split(`{${from}}`).join(`{${to}}`)]),
    );
    return updateSettings(spec, stepId, { headers });
  }
  if (setting === "exits" && Array.isArray(value)) {
    const exits = value.map((ex) =>
      ex.when?.field === from ? { ...ex, when: { ...ex.when, field: to } } : ex,
    );
    return updateSettings(spec, stepId, { exits });
  }
  return spec;
}

/** Lay out steps left to right with dagre. */
export function autoLayout(spec: FlowSpec): FlowSpec {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 50, ranksep: 90, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const step of spec.steps) {
    const exits = exitLabels(step).length;
    g.setNode(step.id, {
      width: exits ? DECISION_WIDTH : NODE_WIDTH,
      height: exits ? 80 + exits * 26 : NODE_HEIGHT,
    });
  }
  for (const c of spec.connections) if (getStep(spec, c.from) && getStep(spec, c.to)) g.setEdge(c.from, c.to);
  dagre.layout(g);
  const positions: Record<string, Position> = {};
  for (const step of spec.steps) {
    const node = g.node(step.id);
    if (node) positions[step.id] = { x: node.x - node.width / 2, y: node.y - node.height / 2 };
  }
  return moveSteps(spec, positions);
}

/** A free spot to the right of the right-most step. */
export function nextFreePosition(spec: FlowSpec): Position {
  const xs = Object.values(spec.canvas.steps);
  if (!xs.length) return { x: 0, y: 120 };
  const maxX = Math.max(...xs.map((p) => p.x));
  const ys = xs.filter((p) => p.x === maxX).map((p) => p.y);
  return { x: maxX + NODE_WIDTH + 60, y: ys[0] ?? 120 };
}

export interface Clipboard {
  steps: Step[];
  connections: Connection[];
  positions: Record<string, Position>;
}

export function copySteps(spec: FlowSpec, ids: string[]): Clipboard {
  const keep = new Set(ids);
  return {
    steps: clone(spec.steps.filter((s) => keep.has(s.id) && s.type !== "input")),
    connections: clone(spec.connections.filter((c) => keep.has(c.from) && keep.has(c.to))),
    positions: Object.fromEntries(ids.filter((id) => spec.canvas.steps[id]).map((id) => [id, spec.canvas.steps[id]])),
  };
}

/** Paste copied steps with fresh ids; returns the new spec and the pasted ids. */
export function pasteSteps(spec: FlowSpec, clip: Clipboard, offset = 40): { spec: FlowSpec; ids: string[] } {
  const next = clone(spec);
  const taken = usedNames(next);
  const remap: Record<string, string> = {};
  for (const step of clip.steps) {
    const id = uniqueName(step.id, taken);
    taken.add(id);
    remap[step.id] = id;
    next.steps.push({ ...clone(step), id });
    const pos = clip.positions[step.id] ?? { x: 0, y: 0 };
    next.canvas.steps[id] = { x: pos.x + offset, y: pos.y + offset };
  }
  for (const conn of clip.connections) {
    next.connections.push({ ...conn, from: remap[conn.from], to: remap[conn.to] });
  }
  return { spec: next, ids: Object.values(remap) };
}

export const STEP_TYPES_WITH_OUTPUT: StepType[] = ["ai_model", "instructions", "http_request", "code", "subflow"];

/** Flow Data fields declared in the Flow Data panel. */
export function setDataFields(spec: FlowSpec, data: FlowSpec["data"]): FlowSpec {
  return { ...clone(spec), data: clone(data) };
}

export function setFlowSettings(spec: FlowSpec, patch: Record<string, unknown>): FlowSpec {
  const next = clone(spec);
  next.settings = { ...(next.settings ?? {}), ...patch };
  for (const [k, v] of Object.entries(next.settings)) if (v === null || v === undefined || v === "") delete (next.settings as Record<string, unknown>)[k];
  return next;
}

export function setRunPolicy(spec: FlowSpec, id: string, patch: Record<string, unknown>): FlowSpec {
  const next = clone(spec);
  const step = next.steps.find((s) => s.id === id);
  if (!step) return spec;
  const policy: Record<string, unknown> = { ...(step.run ?? {}), ...patch };
  for (const [k, v] of Object.entries(policy)) if (v === null || v === undefined || v === "" || v === false || v === 0) delete policy[k];
  if (Object.keys(policy).length) step.run = policy;
  else delete step.run;
  return next;
}
