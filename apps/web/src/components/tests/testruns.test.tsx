import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { TestRunDetail, TestRunSummary } from "../../lib/types";
import { TestRuns } from "./TestRuns";

vi.mock("../../lib/api", async (original) => ({
  ...(await original<typeof import("../../lib/api")>()),
  api: { testRuns: vi.fn(), testRun: vi.fn(), startTestRun: vi.fn(), makeBaseline: vi.fn() },
}));

const mocked = api as unknown as Record<"testRuns" | "testRun" | "startTestRun" | "makeBaseline", ReturnType<typeof vi.fn>>;

const run = (id: string, extra: Partial<TestRunSummary> = {}): TestRunSummary => ({
  id,
  flow_id: "f1",
  version_id: "v1",
  status: "done",
  stand_in: false,
  repeat: 3,
  by: "Olive",
  baseline: false,
  created_at: Date.now() / 1000,
  finished_at: Date.now() / 1000,
  error: null,
  progress: { done: 6, total: 6 },
  summary: { total: 2, passed: 1, failed: 1, flaky: 1, skipped: 0, seconds: 3, tokens: 400, cost: 0.002 },
  ...extra,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("kept Test Runs", () => {
  it("shows each run, compares it with the baseline, and makes a new baseline", async () => {
    mocked.testRuns.mockResolvedValue([run("r2"), run("r1", { baseline: true, summary: { total: 2, passed: 2, failed: 0, flaky: 0, skipped: 0, seconds: 2, tokens: 300, cost: 0.001 } })]);
    const detail: TestRunDetail = {
      ...run("r2"),
      baseline_id: "r1",
      cases: [
        { name: "Refunds", runs: 3, passed: 3, pass_rate: 1, flaky: false, failures: [], skipped: [], seconds: 1.5, tokens: 200, cost: 0.001 },
        { name: "Shipping", runs: 3, passed: 2, pass_rate: 0.6667, flaky: true, failures: ["output.answer: expected to contain 'Germany'"], skipped: [], seconds: 1.5, tokens: 200, cost: 0.001 },
      ],
      comparison: {
        cases: [
          { name: "Refunds", change: "same", before: 1, after: 1, seconds: 0.5, cost: 0 },
          { name: "Shipping", change: "worse", before: 1, after: 0.6667, seconds: 0, cost: 0 },
        ],
        better: 0,
        worse: 1,
        same: 1,
        new: 0,
        gone: 0,
        regressions: ["Shipping"],
      },
    };
    mocked.testRun.mockResolvedValue(detail);
    mocked.makeBaseline.mockResolvedValue({ baseline: "r2" });
    render(<TestRuns flowId="f1" disabled={false} variables={{}} />);
    expect(await screen.findByText("1/2 passed")).toBeTruthy();
    expect(screen.getByText("Baseline")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { expanded: false })[0]);
    expect(await screen.findByText("Against the baseline: 0 better, 1 worse, 1 the same.")).toBeTruthy();
    expect(screen.getByText("Worse")).toBeTruthy();
    expect(screen.getByText("Flaky")).toBeTruthy();
    expect(screen.getByText("100% → 67%")).toBeTruthy();
    expect(screen.getByText("+0.5 s")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Make this the baseline" }));
    await waitFor(() => expect(mocked.makeBaseline).toHaveBeenCalledWith("r2"));
  });

  it("starts a run with repeats and shows its progress", async () => {
    mocked.testRuns.mockResolvedValueOnce([]).mockResolvedValue([run("r3", { status: "running", summary: null, progress: { done: 2, total: 6 } })]);
    mocked.startTestRun.mockResolvedValue({ id: "r3", status: "queued" });
    render(<TestRuns flowId="f1" disabled={false} variables={{ base_url: "http://x" }} />);
    expect(await screen.findByText("None yet.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Repeat each case"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Run and keep" }));
    await waitFor(() => expect(mocked.startTestRun).toHaveBeenCalledWith("f1", { repeat: 3, variables: { base_url: "http://x" } }));
    expect(await screen.findByText("2/6")).toBeTruthy();
  });
});
