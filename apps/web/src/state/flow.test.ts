import { beforeEach, describe, expect, it } from "vitest";
import type { FlowSpec } from "../lib/types";
import { redo, undo, useFlow } from "./flow";

const spec: FlowSpec = { version: 1, name: "A", description: "", data: [], steps: [], connections: [], canvas: { steps: {}, notes: [] } };

describe("flow store history", () => {
  beforeEach(() => useFlow.getState().load("f", spec));

  it("records each edit and supports undo/redo", () => {
    const { apply } = useFlow.getState();
    apply((s) => ({ ...s, name: "B" }));
    apply((s) => ({ ...s, name: "C" }));
    expect(useFlow.getState().saveState).toBe("unsaved");
    undo();
    expect(useFlow.getState().spec!.name).toBe("B");
    undo();
    expect(useFlow.getState().spec!.name).toBe("A");
    redo();
    expect(useFlow.getState().spec!.name).toBe("B");
  });

  it("groups typing in one field into one undo step", () => {
    const { apply } = useFlow.getState();
    for (const name of ["H", "He", "Hel", "Hello"]) apply((s) => ({ ...s, name }), "flow:name");
    apply((s) => ({ ...s, description: "x" }), "flow:description");
    undo();
    expect(useFlow.getState().spec!.name).toBe("Hello");
    undo();
    expect(useFlow.getState().spec!.name).toBe("A");
  });

  it("does not record save-state changes or no-op edits", () => {
    const { apply, setSaveState } = useFlow.getState();
    setSaveState("saving");
    apply((s) => s);
    expect(useFlow.temporal.getState().pastStates).toHaveLength(0);
  });
});
