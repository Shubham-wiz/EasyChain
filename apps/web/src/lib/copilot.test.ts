import { describe, expect, it } from "vitest";
import { applyChanges, marks, type Change } from "./copilot";
import type { FlowSpec, Step } from "./types";

const step = (id: string, type: Step["type"], settings: Step["settings"] = {}): Step => ({ id, type, name: id, description: "", settings });

const spec: FlowSpec = {
  version: 1,
  name: "Reply",
  description: "",
  data: [{ name: "question", type: "text", update: "replace", description: "" }],
  steps: [step("input", "input"), step("answer", "ai_model", { prompt: "{question}" }), step("output", "output")],
  connections: [
    { from: "input", to: "answer", exit: null },
    { from: "answer", to: "output", exit: null },
  ],
  canvas: { steps: { input: { x: 0, y: 0 }, answer: { x: 300, y: 0 }, output: { x: 600, y: 0 } }, notes: [] },
};

const changes: Change[] = [
  { id: "c1", label: "Rename the flow", op: "set_flow", name: "Reply with a summary" },
  { id: "c2", label: "Add Flow Data: summary", op: "set_data", field: { name: "summary", type: "text", update: "replace", description: "" } },
  { id: "c3", label: "Add Summarise", op: "add_step", step: step("summarise", "ai_model", { prompt: "Sum up {answer}" }), position: { x: 900, y: 0 } },
  { id: "c4", label: "Change the answer's prompt", op: "update_step", step: step("answer", "ai_model", { prompt: "Answer: {question}" }) },
  { id: "c5", label: "Connect Answer to Summarise", op: "add_connection", connection: { from: "answer", to: "summarise" } },
  { id: "c6", label: "Connect Summarise to Output", op: "add_connection", connection: { from: "summarise", to: "output" } },
  { id: "c7", label: "Disconnect Answer from Output", op: "remove_connection", connection: { from: "answer", to: "output", exit: null } },
];

describe("the copilot's changes", () => {
  it("apply in order, without touching the flow they started from", () => {
    const next = applyChanges(spec, changes);
    expect(next.name).toBe("Reply with a summary");
    expect(next.data.map((d) => d.name)).toEqual(["question", "summary"]);
    expect(next.steps.map((s) => s.id)).toEqual(["input", "answer", "output", "summarise"]);
    expect(next.steps[1].settings.prompt).toBe("Answer: {question}");
    expect(next.canvas.steps.summarise).toEqual({ x: 900, y: 0 });
    expect(next.connections).toEqual([
      { from: "input", to: "answer", exit: null },
      { from: "answer", to: "summarise", exit: null },
      { from: "summarise", to: "output", exit: null },
    ]);
    expect(spec.steps).toHaveLength(3);
    expect(spec.name).toBe("Reply");
  });

  it("leave out a connection to a step whose change isn't kept", () => {
    const next = applyChanges(
      spec,
      changes.filter((c) => c.id !== "c3"),
    );
    expect(next.steps.map((s) => s.id)).not.toContain("summarise");
    expect(next.connections.some((c) => c.to === "summarise" || c.from === "summarise")).toBe(false);
  });

  it("remove steps with their connections, and Flow Data fields", () => {
    const next = applyChanges(spec, [
      { id: "r1", label: "Remove Answer", op: "remove_step", step: "answer" },
      { id: "r2", label: "Remove question", op: "remove_data", name: "question" },
    ]);
    expect(next.steps.map((s) => s.id)).toEqual(["input", "output"]);
    expect(next.connections).toEqual([]);
    expect(next.canvas.steps.answer).toBeUndefined();
    expect(next.data).toEqual([]);
  });

  it("move steps to make room, but not steps that are gone", () => {
    const next = applyChanges(spec, [
      { id: "m1", label: "Remove Answer", op: "remove_step", step: "answer" },
      { id: "m2", label: "Make room", op: "move_steps", positions: { output: { x: 900, y: 0 }, answer: { x: 600, y: 0 } } },
    ]);
    expect(next.canvas.steps).toEqual({ input: { x: 0, y: 0 }, output: { x: 900, y: 0 } });
  });

  it("don't add a step or a connection twice", () => {
    const twice = applyChanges(spec, [changes[2], changes[2], changes[4], changes[4]]);
    expect(twice.steps.filter((s) => s.id === "summarise")).toHaveLength(1);
    expect(twice.connections.filter((c) => c.to === "summarise")).toHaveLength(1);
  });

  it("mark the steps they add or change", () => {
    expect(marks(changes)).toEqual({ summarise: "added", answer: "changed" });
  });
});
