import { describe, expect, it } from "vitest";
import {
  addStep,
  autoLayout,
  canConnect,
  connect,
  copySteps,
  createStep,
  exitLabels,
  insertBefore,
  pasteSteps,
  removeConnection,
  removeSteps,
  renameStepId,
  renameVariable,
  toIdent,
  updateSettings,
  usedNames,
} from "./spec";
import type { FlowSpec, StepTypeInfo } from "./types";

const info = (type: string, label: string, defaults: Record<string, unknown> = {}): StepTypeInfo => ({
  type: type as StepTypeInfo["type"],
  label,
  technical: "",
  category: "ai",
  icon: "",
  summary: "",
  beginner: true,
  default_name: label,
  defaults,
  form: [],
  docs: "",
});

function blank(): FlowSpec {
  return {
    version: 1,
    name: "Test",
    description: "",
    data: [],
    steps: [
      { id: "input", type: "input", name: "Input", description: "", settings: { mode: "form", fields: [{ name: "question" }] } },
      { id: "output", type: "output", name: "Output", description: "", settings: { fields: [] } },
    ],
    connections: [],
    canvas: { steps: { input: { x: 0, y: 0 }, output: { x: 600, y: 0 } }, notes: [] },
  };
}

describe("ids and names", () => {
  it("makes identifiers from labels", () => {
    expect(toIdent("Fetch the page!")).toBe("fetch_the_page");
    expect(toIdent("123 go")).toBe("step_123_go");
    expect(toIdent("")).toBe("step");
  });

  it("avoids step ids, field names and reserved words", () => {
    const spec = blank();
    const taken = usedNames(spec);
    expect(taken.has("question")).toBe(true);
    expect(taken.has("graph")).toBe(true);
    const ai = createStep(info("ai_model", "AI Model", { save_as: "answer" }), spec);
    expect(ai.id).toBe("ai_model");
    const spec2 = addStep(spec, ai, { x: 1, y: 2 });
    expect(createStep(info("ai_model", "AI Model"), spec2).id).toBe("ai_model_2");
    expect(createStep(info("code", "Answer"), spec2).id).toBe("answer_2");
  });
});

describe("editing", () => {
  it("adds, connects and removes without mutating the input", () => {
    const spec = blank();
    const step = createStep(info("ai_model", "AI Model"), spec);
    const added = addStep(spec, step, { x: 300, y: 0 }, { step: "input" });
    expect(spec.steps).toHaveLength(2);
    expect(added.connections).toEqual([{ from: "input", to: "ai_model", exit: null }]);
    const wired = connect(added, "ai_model", "output");
    expect(wired.connections).toHaveLength(2);
    expect(connect(wired, "ai_model", "output")).toBe(wired); // duplicate refused
    const unwired = removeConnection(wired, { from: "ai_model", to: "output" });
    expect(unwired.connections).toHaveLength(1);
    const removed = removeSteps(wired, ["ai_model"]);
    expect(removed.connections).toEqual([]);
    expect(removed.canvas.steps.ai_model).toBeUndefined();
  });

  it("explains invalid connections", () => {
    let spec = blank();
    spec = addStep(spec, { id: "d", type: "decision", name: "D", description: "", settings: { exits: [{ label: "Yes" }], otherwise: "No" } }, { x: 0, y: 0 });
    expect(canConnect(spec, "output", "d", null)).toMatch(/end of the flow/);
    expect(canConnect(spec, "d", "input", "Yes")).toMatch(/Input/);
    expect(canConnect(spec, "input", "output", null)).toMatch(/at least one step/);
    expect(canConnect(spec, "d", "d", "Yes")).toMatch(/itself/);
    expect(canConnect(spec, "d", "output", null)).toMatch(/exits/);
    const one = connect(spec, "d", "output", "Yes");
    expect(canConnect(one, "d", "output", "Yes")).toMatch(/already leads/);
    expect(canConnect(one, "d", "output", "No")).toBeNull();
  });

  it("keeps decision connections attached when an exit is renamed", () => {
    let spec = blank();
    spec = addStep(spec, { id: "d", type: "decision", name: "D", description: "", settings: { exits: [{ label: "Yes" }], otherwise: "No" } }, { x: 0, y: 0 });
    spec = connect(spec, "d", "output", "Yes");
    spec = updateSettings(spec, "d", { exits: [{ label: "Sure" }] });
    expect(spec.connections[0].exit).toBe("Sure");
    expect(exitLabels(spec.steps[2])).toEqual(["Sure", "No"]);
  });

  it("keeps each connection on its own exit when exits move or are deleted", () => {
    let spec = blank();
    spec = addStep(spec, { id: "x", type: "code", name: "X", description: "", settings: {} }, { x: 0, y: 0 });
    spec = addStep(spec, { id: "d", type: "decision", name: "D", description: "", settings: { exits: [{ label: "A" }, { label: "B" }], otherwise: "Other" } }, { x: 0, y: 0 });
    spec = connect(spec, "d", "x", "A");
    spec = connect(spec, "d", "output", "B");
    // Moving B above A: A still goes to x, B to output.
    spec = updateSettings(spec, "d", { exits: [{ label: "B" }, { label: "A" }] });
    expect(spec.connections).toContainEqual({ from: "d", to: "x", exit: "A" });
    expect(spec.connections).toContainEqual({ from: "d", to: "output", exit: "B" });
    // Deleting A deletes its connection.
    spec = updateSettings(spec, "d", { exits: [{ label: "B" }] });
    expect(spec.connections.filter((c) => c.from === "d")).toEqual([{ from: "d", to: "output", exit: "B" }]);
  });

  it("turns labelled connections plain when a step stops having exits", () => {
    let spec = blank();
    spec = addStep(spec, { id: "ask", type: "ask_human", name: "Ask", description: "", settings: { kind: "approve" } }, { x: 0, y: 0 });
    spec = connect(spec, "ask", "output", "Approved");
    spec = updateSettings(spec, "ask", { kind: "answer" });
    expect(spec.connections).toContainEqual({ from: "ask", to: "output", exit: null });
  });

  it("renames ids everywhere and inserts steps before others", () => {
    let spec = blank();
    spec = addStep(spec, { id: "ask", type: "ai_model", name: "Ask", description: "", settings: {} }, { x: 300, y: 0 }, { step: "input" });
    spec = connect(spec, "ask", "output");
    const renamed = renameStepId(spec, "ask", "ask_ai");
    expect(renamed.connections.map((c) => [c.from, c.to])).toEqual([
      ["input", "ask_ai"],
      ["ask_ai", "output"],
    ]);
    expect(renamed.canvas.steps.ask_ai).toEqual({ x: 300, y: 0 });
    expect(renameStepId(renamed, "ask_ai", "input")).toBe(renamed);
    const inserted = insertBefore(renamed, "ask_ai", { id: "prompt", type: "instructions", name: "P", description: "", settings: {} });
    expect(inserted.connections.map((c) => [c.from, c.to])).toEqual([
      ["input", "prompt"],
      ["ask_ai", "output"],
      ["prompt", "ask_ai"],
    ]);
    expect(inserted.canvas.steps.ask_ai.x).toBeGreaterThan(inserted.canvas.steps.prompt.x);
  });

  it("renames variables in templates, headers and rules", () => {
    let spec = blank();
    spec = addStep(spec, { id: "p", type: "instructions", name: "P", description: "", settings: { user: "Use {pgae} and {pgae}" } }, { x: 0, y: 0 });
    expect(renameVariable(spec, "p", "user", "pgae", "page").steps[2].settings.user).toBe("Use {page} and {page}");
    spec = addStep(spec, { id: "h", type: "http_request", name: "H", description: "", settings: { headers: { A: "{tok}" } } }, { x: 0, y: 0 });
    expect(renameVariable(spec, "h", "headers", "tok", "token").steps[3].settings.headers).toEqual({ A: "{token}" });
    spec = addStep(spec, { id: "d", type: "decision", name: "D", description: "", settings: { exits: [{ label: "A", when: { field: "x", op: "is_empty" } }] } }, { x: 0, y: 0 });
    expect(renameVariable(spec, "d", "exits", "x", "y").steps[4].settings.exits[0].when.field).toBe("y");
  });

  it("copies and pastes with fresh ids and internal connections", () => {
    let spec = blank();
    spec = addStep(spec, { id: "a", type: "code", name: "A", description: "", settings: {} }, { x: 100, y: 0 });
    spec = addStep(spec, { id: "b", type: "code", name: "B", description: "", settings: {} }, { x: 300, y: 0 });
    spec = connect(spec, "a", "b");
    const clip = copySteps(spec, ["input", "a", "b"]);
    expect(clip.steps.map((s) => s.id)).toEqual(["a", "b"]);
    const { spec: pasted, ids } = pasteSteps(spec, clip);
    expect(ids).toEqual(["a_2", "b_2"]);
    expect(pasted.connections).toContainEqual({ from: "a_2", to: "b_2", exit: null });
    expect(pasted.canvas.steps.a_2).toEqual({ x: 140, y: 40 });
  });

  it("leaves Input's connections out of a paste", () => {
    let spec = blank();
    spec = addStep(spec, { id: "a", type: "code", name: "A", description: "", settings: {} }, { x: 100, y: 0 }, { step: "input" });
    const { spec: pasted } = pasteSteps(spec, copySteps(spec, ["input", "a"]));
    expect(pasted.connections.every((c) => c.from && c.to)).toBe(true);
    expect(pasted.connections).toHaveLength(1);
  });

  it("gives a pasted agent the pasted copies of its tools", () => {
    let spec = blank();
    spec = addStep(spec, { id: "lookup", type: "http_request", name: "Lookup", description: "", settings: {} }, { x: 0, y: 0 });
    spec = addStep(spec, { id: "other", type: "http_request", name: "Other", description: "", settings: {} }, { x: 0, y: 0 });
    spec = addStep(spec, { id: "helper", type: "agent", name: "Helper", description: "", settings: { tools: ["lookup", "other"], addons: { approve_tools: ["lookup"] } } }, { x: 0, y: 0 });
    const { spec: pasted } = pasteSteps(spec, copySteps(spec, ["helper", "lookup"]));
    const agent = pasted.steps.find((s) => s.id === "helper_2")!;
    expect(agent.settings.tools).toEqual(["lookup_2"]);
    expect(agent.settings.addons.approve_tools).toEqual(["lookup_2"]);
  });

  it("lays out left to right", () => {
    let spec = blank();
    spec = addStep(spec, { id: "a", type: "code", name: "A", description: "", settings: {} }, { x: 0, y: 500 }, { step: "input" });
    spec = connect(spec, "a", "output");
    const laid = autoLayout(spec);
    expect(laid.canvas.steps.input.x).toBeLessThan(laid.canvas.steps.a.x);
    expect(laid.canvas.steps.a.x).toBeLessThan(laid.canvas.steps.output.x);
  });
});
