import { describe, expect, it } from "vitest";
import { canConnect, connect, EACH_ITEM, exitLabels, hasExits, setDataFields, setFlowSettings, setRunPolicy, updateSettings, WHEN_DONE } from "./spec";
import type { FlowSpec, Step } from "./types";

function step(id: string, type: Step["type"], settings: Record<string, unknown> = {}): Step {
  return { id, type, name: id, description: "", settings };
}

function flow(...steps: Step[]): FlowSpec {
  return { version: 1, name: "T", description: "", data: [], steps, connections: [], canvas: { steps: {}, notes: [] } };
}

describe("exits of Phase 2 steps", () => {
  it("lists each step type's exits", () => {
    expect(exitLabels(step("a", "ask_human", { kind: "approve" }))).toEqual(["Approved", "Rejected"]);
    expect(exitLabels(step("a", "ask_human", { kind: "choose", options: ["Yes", "No", "Yes"] }))).toEqual(["Yes", "No"]);
    expect(exitLabels(step("a", "ask_human", { kind: "answer" }))).toEqual([]);
    expect(exitLabels(step("f", "for_each"))).toEqual([EACH_ITEM, WHEN_DONE]);
    expect(exitLabels(step("j", "jump", { exits: [{ label: "Again" }], otherwise: "Done" }))).toEqual(["Again", "Done"]);
    expect(hasExits(step("c", "code"))).toBe(false);
  });

  it("keeps connections when a choice is renamed", () => {
    let spec = flow(step("in", "input"), step("ask", "ask_human", { kind: "choose", options: ["A", "B"] }), step("x", "code"));
    spec = connect(spec, "ask", "x", "B");
    spec = updateSettings(spec, "ask", { options: ["A", "Bee"] });
    expect(spec.connections[0].exit).toBe("Bee");
  });
});

describe("For Each connections", () => {
  const base = () => flow(step("in", "input"), step("each", "for_each"), step("work", "code"), step("next", "code"), step("out", "output"), step("pick", "decision", { exits: [], otherwise: "Otherwise" }));

  it("needs an exit and a plain step per item", () => {
    const spec = base();
    expect(canConnect(spec, "each", "work", null)).toMatch(/exits/);
    expect(canConnect(spec, "each", "work", EACH_ITEM)).toBeNull();
    expect(canConnect(spec, "each", "out", EACH_ITEM)).toMatch(/one action/);
    expect(canConnect(spec, "each", "pick", EACH_ITEM)).toMatch(/one action/);
  });

  it("keeps the per-item step to itself", () => {
    let spec = connect(base(), "each", "work", EACH_ITEM);
    expect(canConnect(spec, "work", "next", null)).toMatch(/once per item/);
    expect(canConnect(spec, "in", "work", null)).toMatch(/nothing else/);
    spec = connect(spec, "each", "next", WHEN_DONE);
    expect(spec.connections).toHaveLength(2);
  });
});

describe("flow settings, Flow Data and run policies", () => {
  it("drops empty settings and policies", () => {
    let spec = flow(step("in", "input"), step("c", "code"));
    spec = setFlowSettings(spec, { max_parallel: 3, max_concurrent_runs: null });
    expect(spec.settings).toEqual({ max_parallel: 3 });
    spec = setRunPolicy(spec, "c", { retries: 2, cache: false });
    expect(spec.steps[1].run).toEqual({ retries: 2 });
    spec = setRunPolicy(spec, "c", { retries: 0 });
    expect(spec.steps[1].run).toBeUndefined();
    spec = setDataFields(spec, [{ name: "tags", type: "list", update: "append", description: "" }]);
    expect(spec.data[0].update).toBe("append");
  });
});
