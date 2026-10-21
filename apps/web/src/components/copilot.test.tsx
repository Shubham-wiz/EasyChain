import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import type { Proposal } from "../lib/copilot";
import type { FlowSpec } from "../lib/types";
import { useCopilot } from "../state/copilot";
import { undo, useFlow } from "../state/flow";
import { useUi } from "../state/ui";
import { CopilotPanel } from "./CopilotPanel";
import { TooltipProvider } from "./ui";

vi.mock("../lib/api", async (original) => ({
  ...(await original<typeof import("../lib/api")>()),
  api: {
    copilot: vi.fn(),
    copilotSettings: vi.fn(async () => ({ model: null, using: null })),
    saveCopilotSettings: vi.fn(),
    addTestCases: vi.fn(async () => ({ added: 1, total: 1 })),
    inbox: vi.fn(async () => []),
  },
}));

const mocked = api as unknown as Record<"copilot" | "addTestCases", ReturnType<typeof vi.fn>>;

const spec: FlowSpec = {
  version: 1,
  name: "Untitled flow",
  description: "",
  data: [],
  steps: [
    { id: "input", type: "input", name: "Input", description: "", settings: {} },
    { id: "output", type: "output", name: "Output", description: "", settings: {} },
  ],
  connections: [{ from: "input", to: "output", exit: null }],
  canvas: { steps: { input: { x: 0, y: 0 }, output: { x: 600, y: 0 } }, notes: [] },
};

const proposal: Proposal = {
  ok: true,
  summary: "Summarises the page at a link.",
  spec: null,
  changes: [
    { id: "c1", label: "Rename the flow to “Summarise a page”", op: "set_flow", name: "Summarise a page" },
    {
      id: "c2",
      label: "Add the AI Model step “Summarise”",
      op: "add_step",
      step: { id: "summarise", type: "ai_model", name: "Summarise", description: "", settings: { save_as: "summary" } },
      position: { x: 300, y: 0 },
    },
    { id: "c3", label: "Connect “Input” to “Summarise”", op: "add_connection", connection: { from: "input", to: "summarise" } },
  ],
  test_cases: [
    { name: "A short page", inputs: { url: "https://example.com" } },
    { name: "A long page", inputs: { url: "https://example.org" } },
  ],
  issues: [],
  model: "stand-in",
  attempts: 1,
  problems: [],
};

function show() {
  render(
    <TooltipProvider>
      <CopilotPanel />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  useFlow.getState().load("f1", spec);
  useCopilot.getState().discard();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the copilot", () => {
  it("previews the kept changes, then applies them as one undo step with the kept test cases", async () => {
    mocked.copilot.mockResolvedValue(proposal);
    show();
    expect(await screen.findByText(/stand-in copilot/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Describe the flow"), { target: { value: "Summarise a web page" } });
    fireEvent.click(screen.getByTestId("copilot-propose"));
    expect(await screen.findByText("Summarises the page at a link.")).toBeTruthy();
    expect(mocked.copilot).toHaveBeenCalledWith({ request: "Summarise a web page", spec, issue: undefined });
    expect(useUi.getState().rightTab).toBe("copilot");

    // All the changes are kept at first and shown on the canvas; the flow itself is untouched.
    expect(useCopilot.getState().preview?.steps.map((s) => s.id)).toContain("summarise");
    expect(useCopilot.getState().marks).toEqual({ summarise: "added" });
    expect(useFlow.getState().spec).toEqual(spec);

    // Leave out the rename and the second test case.
    fireEvent.click(screen.getByLabelText("Rename the flow to “Summarise a page”"));
    fireEvent.click(screen.getByLabelText(/A long page/));
    expect(useCopilot.getState().preview?.name).toBe("Untitled flow");
    expect(screen.getByText("2 of 3 kept")).toBeTruthy();

    fireEvent.click(screen.getByTestId("copilot-accept"));
    await waitFor(() => expect(mocked.addTestCases).toHaveBeenCalledWith("f1", [proposal.test_cases[0]]));
    const applied = useFlow.getState().spec!;
    expect(applied.name).toBe("Untitled flow");
    expect(applied.steps.map((s) => s.id)).toEqual(["input", "output", "summarise"]);
    expect(applied.connections).toContainEqual({ from: "input", to: "summarise", exit: null });
    expect(useCopilot.getState().preview).toBeNull();

    undo();
    expect(useFlow.getState().spec).toEqual(spec);
  });

  it("proposes on Ctrl+Enter without running the flow", async () => {
    mocked.copilot.mockResolvedValue(proposal);
    const run = vi.fn();
    window.addEventListener("easychain:run", run);
    const onKey = (e: KeyboardEvent) => e.key === "Enter" && window.dispatchEvent(new Event("easychain:run"));
    window.addEventListener("keydown", onKey);
    show();
    const box = screen.getByLabelText("Describe the flow");
    fireEvent.change(box, { target: { value: "Summarise a web page" } });
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(mocked.copilot).toHaveBeenCalledTimes(1));
    expect(run).not.toHaveBeenCalled();
    window.removeEventListener("keydown", onKey);
    window.removeEventListener("easychain:run", run);
  });

  it("keeps the preview current when the flow changes underneath it", async () => {
    mocked.copilot.mockResolvedValue(proposal);
    await useCopilot.getState().ask("Summarise a web page");
    useFlow.getState().apply((s) => ({ ...s, description: "Edited meanwhile" }));
    expect(useCopilot.getState().preview?.description).toBe("Edited meanwhile");
    expect(useCopilot.getState().preview?.steps.map((s) => s.id)).toContain("summarise");
  });

  it("says why when it can't propose anything, and discards cleanly", async () => {
    mocked.copilot.mockResolvedValue({
      ...proposal,
      ok: false,
      summary: "The stand-in copilot can't fix problems. Use the problem's own fix button, or add an API key.",
      changes: [],
      test_cases: [],
    });
    show();
    await useCopilot.getState().ask("", { level: "error", code: "missing_field", message: "Nothing writes `summary`.", step: "output" });
    expect((await screen.findByTestId("copilot-failed")).textContent).toContain("can't fix problems");
    expect(mocked.copilot.mock.calls[0][0].issue.code).toBe("missing_field");
    fireEvent.click(screen.getByText("OK"));
    expect(useCopilot.getState().proposal).toBeNull();
    expect(screen.queryByTestId("copilot-failed")).toBeNull();
  });
});
