import { describe, expect, it } from "vitest";
import { addImportedSteps, addTool, addToolStep, canAddTool, canConnect, connect, removeSteps, removeTool, renameStepId, toolOf } from "./spec";
import type { FlowSpec, Step } from "./types";

function step(id: string, type: Step["type"], settings: Record<string, unknown> = {}): Step {
  return { id, type, name: id, description: "", settings };
}

function flow(...steps: Step[]): FlowSpec {
  return { version: 1, name: "T", description: "", data: [], steps, connections: [], canvas: { steps: { helper: { x: 100, y: 100 } }, notes: [] } };
}

const base = () =>
  flow(step("in", "input"), step("helper", "agent", { tools: [] }), step("lookup", "http_request"), step("search", "knowledge_search"), step("ask", "ai_model"), step("out", "output"));

describe("agent tools", () => {
  it("adds tool steps to an agent", () => {
    let spec = addTool(base(), "helper", "lookup");
    spec = addTool(spec, "helper", "search");
    expect(spec.steps.find((s) => s.id === "helper")?.settings.tools).toEqual(["lookup", "search"]);
    expect(toolOf(spec, "lookup")).toBe("helper");
    expect(toolOf(spec, "ask")).toBeNull();
  });

  it("explains why a step can't be a tool", () => {
    const spec = connect(base(), "in", "lookup");
    expect(canAddTool(spec, "ask", "helper")).toMatch(/Tools can be/);
    expect(canAddTool(spec, "search", "ask")).toMatch(/Only an Agent/);
    expect(canAddTool(spec, "lookup", "helper")).toMatch(/connections/);
    expect(canAddTool(addTool(base(), "helper", "search"), "search", "helper")).toMatch(/already/);
    expect(canAddTool(spec, "gone", "helper")).toMatch(/no longer exists/);
    expect(addTool(spec, "helper", "ask")).toBe(spec);
  });

  it("keeps tools out of the flow's connections", () => {
    const spec = addTool(base(), "helper", "lookup");
    expect(canConnect(spec, "in", "lookup", null)).toMatch(/tool of the agent/);
    expect(canConnect(spec, "lookup", "out", null)).toMatch(/tool of the agent/);
    expect(canConnect(spec, "in", "helper", null)).toBeNull();
  });

  it("drops a tool and its approval together", () => {
    let spec = addTool(addTool(base(), "helper", "lookup"), "helper", "search");
    spec.steps.find((s) => s.id === "helper")!.settings.addons = { approve_tools: ["lookup"] };
    spec = removeTool(spec, "helper", "lookup");
    const agent = spec.steps.find((s) => s.id === "helper")!;
    expect(agent.settings.tools).toEqual(["search"]);
    expect(agent.settings.addons?.approve_tools).toEqual([]);
  });

  it("forgets deleted tool steps", () => {
    let spec = addTool(base(), "helper", "lookup");
    spec.steps.find((s) => s.id === "helper")!.settings.addons = { approve_tools: ["lookup"], call_limit: 5 };
    spec = removeSteps(spec, ["lookup"]);
    const agent = spec.steps.find((s) => s.id === "helper")!;
    expect(agent.settings.tools).toEqual([]);
    expect(agent.settings.addons).toEqual({ approve_tools: [], call_limit: 5 });
  });

  it("follows a renamed tool step", () => {
    let spec = addTool(base(), "helper", "lookup");
    spec.steps.find((s) => s.id === "helper")!.settings.addons = { approve_tools: ["lookup"] };
    spec = renameStepId(spec, "lookup", "get_order");
    const agent = spec.steps.find((s) => s.id === "helper")!;
    expect(agent.settings.tools).toEqual(["get_order"]);
    expect(agent.settings.addons?.approve_tools).toEqual(["get_order"]);
  });

  it("places a new tool step under its agent", () => {
    const spec = addToolStep(base(), "helper", step("memo", "memory"));
    expect(toolOf(spec, "memo")).toBe("helper");
    expect(spec.canvas.steps.memo.y).toBeGreaterThan(spec.canvas.steps.helper.y);
  });
});

describe("imported API steps", () => {
  const imported = [step("get_order", "http_request"), step("cancel_order", "http_request")];
  const data = [{ name: "order", type: "object" as const, description: "", update: "replace" as const }];

  it("adds steps and their Flow Data once", () => {
    let spec = addImportedSteps(base(), imported, data);
    spec = addImportedSteps(spec, [step("list_orders", "http_request")], data);
    expect(spec.steps.map((s) => s.id)).toEqual(expect.arrayContaining(["get_order", "cancel_order", "list_orders"]));
    expect(spec.data.filter((f) => f.name === "order")).toHaveLength(1);
    expect(toolOf(spec, "get_order")).toBeNull();
  });

  it("can hand them straight to an agent", () => {
    const spec = addImportedSteps(base(), imported, [], "helper");
    expect(spec.steps.find((s) => s.id === "helper")?.settings.tools).toEqual(["get_order", "cancel_order"]);
  });
});
