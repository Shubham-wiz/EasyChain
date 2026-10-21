import { describe, expect, it } from "vitest";
import { useRun } from "../state/run";
import {
  addHelper,
  addHelperStep,
  addTool,
  canAddHelper,
  canAddTool,
  canConnect,
  copySteps,
  helperOf,
  pasteSteps,
  removeHelper,
  removeSteps,
  renameStepId,
  toolOf,
} from "./spec";
import type { FlowSpec, RunEvent, Step } from "./types";

function step(id: string, type: Step["type"], settings: Record<string, unknown> = {}): Step {
  return { id, type, name: id, description: "", settings };
}

function flow(...steps: Step[]): FlowSpec {
  return { version: 1, name: "T", description: "", data: [], steps, connections: [], canvas: { steps: { lead: { x: 100, y: 100 } }, notes: [] } };
}

const base = () =>
  flow(
    step("in", "input"),
    step("lead", "deep_agent", { tools: [], helpers: [] }),
    step("other", "deep_agent", { helpers: [] }),
    step("researcher", "helper", { tools: [] }),
    step("writer", "helper"),
    step("lookup", "http_request"),
    step("agent", "agent"),
    step("out", "output"),
  );

const settings = (spec: FlowSpec, id: string) => spec.steps.find((s) => s.id === id)!.settings;

describe("Deep Agent Helpers", () => {
  it("puts Helpers on a Deep Agent's team", () => {
    let spec = addHelper(base(), "lead", "researcher");
    spec = addHelper(spec, "lead", "writer");
    expect(settings(spec, "lead").helpers).toEqual(["researcher", "writer"]);
    expect(helperOf(spec, "researcher")).toBe("lead");
    expect(helperOf(spec, "lookup")).toBeNull();
  });

  it("explains why a step can't join the team", () => {
    const spec = addHelper(base(), "lead", "researcher");
    expect(canAddHelper(spec, "writer", "agent")).toMatch(/Only a Deep Agent/);
    expect(canAddHelper(spec, "lookup", "lead")).toMatch(/Only a Helper/);
    expect(canAddHelper(spec, "researcher", "lead")).toMatch(/already/);
    expect(canAddHelper(spec, "researcher", "other")).toMatch(/belongs to one Deep Agent/);
    expect(canAddHelper(spec, "gone", "lead")).toMatch(/no longer exists/);
    expect(addHelper(spec, "other", "researcher")).toBe(spec);
  });

  it("keeps Helpers out of the flow's connections", () => {
    const spec = addHelper(base(), "lead", "researcher");
    expect(canConnect(spec, "in", "researcher", null)).toMatch(/Helpers handle/);
    expect(canConnect(spec, "writer", "out", null)).toMatch(/Helpers handle/);
    expect(canConnect(spec, "in", "lead", null)).toBeNull();
  });

  it("gives Deep Agents and Helpers their own tools", () => {
    let spec = addTool(base(), "researcher", "lookup");
    expect(toolOf(spec, "lookup")).toBe("researcher");
    expect(canAddTool(spec, "lookup", "lead")).toBeNull();
    spec = addTool(base(), "lead", "lookup");
    expect(toolOf(spec, "lookup")).toBe("lead");
  });

  it("takes a Helper off the team, and forgets a deleted one", () => {
    let spec = addHelper(addHelper(base(), "lead", "researcher"), "lead", "writer");
    spec = removeHelper(spec, "lead", "writer");
    expect(settings(spec, "lead").helpers).toEqual(["researcher"]);
    spec = removeSteps(spec, ["researcher"]);
    expect(settings(spec, "lead").helpers).toEqual([]);
  });

  it("forgets a Helper's deleted tool and its approval", () => {
    let spec = addTool(base(), "researcher", "lookup");
    settings(spec, "researcher").addons = { approve_tools: ["lookup"] };
    spec = removeSteps(spec, ["lookup"]);
    expect(settings(spec, "researcher").tools).toEqual([]);
    expect(settings(spec, "researcher").addons.approve_tools).toEqual([]);
  });

  it("follows a renamed Helper", () => {
    const spec = renameStepId(addHelper(base(), "lead", "researcher"), "researcher", "finder");
    expect(settings(spec, "lead").helpers).toEqual(["finder"]);
  });

  it("places a new Helper under its Deep Agent", () => {
    const spec = addHelperStep(base(), "lead", step("fresh", "helper"));
    expect(settings(spec, "lead").helpers).toEqual(["fresh"]);
    expect(spec.canvas.steps.fresh.y).toBeGreaterThan(spec.canvas.steps.lead.y);
  });

  it("pastes a Deep Agent with the copies of its Helpers", () => {
    const spec = addHelper(addHelper(base(), "lead", "researcher"), "lead", "writer");
    const { spec: pasted, ids } = pasteSteps(spec, copySteps(spec, ["lead", "researcher"]));
    const lead = pasted.steps.find((s) => s.id === ids[0])!;
    expect(lead.type).toBe("deep_agent");
    expect(lead.settings.helpers).toEqual([ids[1]]);
    expect(settings(pasted, "lead").helpers).toEqual(["researcher", "writer"]);
  });
});

describe("a Deep Agent's run", () => {
  const at = (event: Partial<RunEvent>) => ({ run_id: "r", ts: 1, ...event }) as RunEvent;

  it("nests a Helper's tool calls under its task, with the to-do list and files", () => {
    useRun.getState().reset();
    const handle = useRun.getState().handle;
    handle(at({ type: "step_started", step: "lead", input: {} }));
    handle(at({ type: "todos", step: "lead", todos: [{ content: "Research", status: "in_progress" }] }));
    handle(at({ type: "tool_started", step: "lead", tool: "task", args: { description: "Find" }, call_id: "t1", to_helper: "researcher" }));
    handle(at({ type: "tool_started", step: "lead", tool: "lookup", args: {}, call_id: "c1", helper: "researcher", within: "t1" }));
    handle(at({ type: "todos", step: "lead", todos: [{ content: "inner", status: "pending" }], helper: "researcher" }));
    handle(at({ type: "files", step: "lead", files: { "/notes.md": "a" }, helper: "researcher" }));
    handle(
      at({
        type: "tool_finished",
        step: "lead",
        tool: "task",
        call_id: "t1",
        result: "Found it",
        status: "success",
        duration_ms: 5,
        usage: { input_tokens: 10, output_tokens: 2 },
        to_helper: "researcher",
      }),
    );
    handle(at({ type: "files", step: "lead", files: { "/report.md": "b", "/notes.md": null } }));
    const run = useRun.getState().steps.lead;
    expect(run.todos).toEqual([{ content: "Research", status: "in_progress" }]);
    expect(run.files).toEqual({ "/report.md": "b" });
    const [task, inner] = run.tools!;
    expect(task).toMatchObject({ toHelper: "researcher", status: "done", result: "Found it", usage: { input_tokens: 10, output_tokens: 2 } });
    expect(inner).toMatchObject({ tool: "lookup", helper: "researcher", within: "t1", status: "running" });
  });
});
