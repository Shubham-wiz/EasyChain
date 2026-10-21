// The describe-it copilot's proposals: separate changes to a flow, applied together (one undo).

import { clone, moveSteps, removeSteps, sameConnection as same } from "./spec";
import type { Connection, DataField, FlowSettings, FlowSpec, Issue, Position, Step } from "./types";

export type Change = { id: string; label: string } & (
  | { op: "set_flow"; name?: string; description?: string; settings?: FlowSettings }
  | { op: "set_data"; field: DataField }
  | { op: "remove_data"; name: string }
  | { op: "add_step"; step: Step; position: Position }
  | { op: "update_step"; step: Step }
  | { op: "remove_step"; step: string }
  | { op: "move_steps"; positions: Record<string, Position> }
  | { op: "add_connection"; connection: Connection }
  | { op: "remove_connection"; connection: Connection }
);

export interface TestCase {
  name: string;
  inputs?: Record<string, unknown>;
  expect?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Proposal {
  ok: boolean;
  summary: string;
  spec: FlowSpec | null;
  changes: Change[];
  test_cases: TestCase[];
  issues: Issue[];
  model: string;
  attempts: number;
  problems: string[];
}

/** The flow with these changes applied, in order. A connection to a step that isn't there
 * (its change wasn't kept) is left out. */
export function applyChanges(spec: FlowSpec, changes: Change[]): FlowSpec {
  let next = clone(spec);
  for (const change of changes) {
    switch (change.op) {
      case "set_flow":
        if (change.name != null) next.name = change.name;
        if (change.description != null) next.description = change.description;
        if (change.settings) next.settings = clone(change.settings);
        break;
      case "set_data": {
        const i = next.data.findIndex((d) => d.name === change.field.name);
        if (i === -1) next.data.push(clone(change.field));
        else next.data[i] = clone(change.field);
        break;
      }
      case "remove_data":
        next.data = next.data.filter((d) => d.name !== change.name);
        break;
      case "add_step":
        if (!next.steps.some((s) => s.id === change.step.id)) {
          next.steps.push(clone(change.step));
          next.canvas.steps[change.step.id] = { ...change.position };
        }
        break;
      case "update_step": {
        const i = next.steps.findIndex((s) => s.id === change.step.id);
        if (i !== -1) next.steps[i] = clone(change.step);
        break;
      }
      case "remove_step":
        next = removeSteps(next, [change.step]);
        break;
      case "move_steps":
        next = moveSteps(
          next,
          Object.fromEntries(Object.entries(change.positions).filter(([id]) => next.steps.some((s) => s.id === id))),
        );
        break;
      case "add_connection": {
        const c = change.connection;
        const ends = next.steps.some((s) => s.id === c.from) && next.steps.some((s) => s.id === c.to);
        if (ends && !next.connections.some((x) => same(x, c))) next.connections.push({ ...c, exit: c.exit ?? null });
        break;
      }
      case "remove_connection":
        next.connections = next.connections.filter((x) => !same(x, change.connection));
        break;
    }
  }
  return next;
}

export type Mark = "added" | "changed";

/** How each step looks in the preview: new, or changed. */
export function marks(changes: Change[]): Record<string, Mark> {
  const out: Record<string, Mark> = {};
  for (const change of changes) {
    if (change.op === "add_step") out[change.step.id] = "added";
    else if (change.op === "update_step") out[change.step.id] = "changed";
  }
  return out;
}
