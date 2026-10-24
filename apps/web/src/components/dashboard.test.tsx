import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import type { Dashboard as DashboardData, DashboardDay } from "../lib/types";
import { compact, niceTicks } from "./charts";
import { Dashboard } from "./Dashboard";
import { TracingSection } from "./dialogs/Tracing";
import { TooltipProvider } from "./ui";

vi.mock("../lib/api", async (original) => ({
  ...(await original<typeof import("../lib/api")>()),
  api: { dashboard: vi.fn(), flows: vi.fn(), tracing: vi.fn(), saveTracing: vi.fn(), testTracing: vi.fn(), inbox: vi.fn(() => Promise.resolve([])) },
}));

vi.mock("../state/auth", async (original) => ({
  ...(await original<typeof import("../state/auth")>()),
  useCan: () => true,
}));

const mocked = api as unknown as Record<"dashboard" | "flows" | "tracing" | "saveTracing" | "testTracing", ReturnType<typeof vi.fn>>;

const day = (n: number, extra: Partial<DashboardDay> = {}): DashboardDay => ({
  day: `2026-10-${String(20 + n).padStart(2, "0")}`,
  start: 0,
  runs: 0,
  ok: 0,
  errors: 0,
  paused: 0,
  cancelled: 0,
  running: 0,
  input_tokens: 0,
  output_tokens: 0,
  cost: 0,
  p50_ms: null,
  p95_ms: null,
  ...extra,
});

const data: DashboardData = {
  days: 7,
  since: 0,
  flow_id: null,
  environment: null,
  totals: { runs: 12, ok: 9, errors: 3, paused: 0, cancelled: 0, running: 0, input_tokens: 4200, output_tokens: 800, cost: 1.25, p50_ms: 900, p95_ms: 4200, error_rate: 0.25 },
  series: [day(0), day(1), day(2), day(3, { runs: 4, ok: 3, errors: 1, p50_ms: 800, p95_ms: 2000, input_tokens: 1000, cost: 0.5 }), day(4), day(5), day(6, { runs: 8, ok: 6, errors: 2, p50_ms: 1000, p95_ms: 4200, input_tokens: 3200, output_tokens: 800, cost: 0.75 })],
  flows: [{ flow_id: "support", flow: "Support bot", runs: 12, ok: 9, errors: 3, paused: 0, cancelled: 0, running: 0, input_tokens: 4200, output_tokens: 800, cost: 1.25, p95_ms: 4200, error_rate: 0.25 }],
  errors: [{ flow_id: "support", flow: "Support bot", kind: "rate_limit", message: "OpenAI is limiting requests right now.", count: 3, last_at: 1_790_000_000, last_run_id: "r9" }],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("charts", () => {
  it("rounds ticks and writes numbers compactly", () => {
    expect(niceTicks(0)).toEqual([0, 1]);
    expect(niceTicks(9)).toEqual([0, 5, 10]);
    expect(niceTicks(4200)).toEqual([0, 2000, 4000, 6000]);
    expect(niceTicks(0.7)).toEqual([0, 0.2, 0.4, 0.6, 0.8]);
    expect(compact(1500)).toBe("1.5K");
    expect(compact(25_000)).toBe("25K");
    expect(compact(1000)).toBe("1K");
    expect(compact(0.5)).toBe("0.5");
    expect(niceTicks(2, 4, true)).toEqual([0, 1, 2]);
    expect(compact(0.25)).toBe("0.25");
  });
});

describe("the dashboard", () => {
  it("shows the headline numbers, the charts, the flows, the errors and each day", async () => {
    mocked.flows.mockResolvedValue([{ id: "support", name: "Support bot" }]);
    mocked.dashboard.mockResolvedValue(data);
    render(
      <TooltipProvider>
        <Dashboard />
      </TooltipProvider>,
    );
    expect(await screen.findByTestId("tile-runs")).toHaveProperty("textContent", "Runs12");
    expect(screen.getByTestId("tile-errors").textContent).toBe("Failed25%3 of 12 finished");
    expect(screen.getByTestId("tile-latency").textContent).toContain("4.2 s");
    expect(screen.getByTestId("tile-cost").textContent).toContain("$1.25");
    for (const name of ["Runs a day", "Time to finish, per day", "Tokens a day", "Cost a day"]) {
      expect(screen.getByRole("img", { name })).toBeTruthy();
    }
    // Two series: a legend names them.
    const runsCard = screen.getByRole("region", { name: "Runs a day" });
    expect(within(runsCard).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Succeeded or waiting", "Failed"]);
    expect(within(screen.getByTestId("dashboard-flows")).getByRole("link", { name: "Support bot" }).getAttribute("href")).toBe("#/flows/support");
    expect(screen.getByTestId("dashboard-errors").textContent).toContain("3× OpenAI is limiting requests right now.");
    const rows = within(screen.getByTestId("dashboard-days")).getAllByRole("row");
    expect(rows).toHaveLength(8);
    expect(rows[1].textContent).toContain("2026-10-26");

    // The keyboard reads each day.
    const chart = screen.getByLabelText("Runs a day: use the arrow keys to read each day");
    fireEvent.keyDown(chart, { key: "ArrowLeft" });
    const readout = await screen.findByRole("status");
    expect(readout.textContent).toContain("Oct 25");
    fireEvent.keyDown(chart, { key: "ArrowLeft" });
    fireEvent.keyDown(chart, { key: "ArrowLeft" });
    expect(screen.getByRole("status").textContent).toMatch(/Oct 23.*3Succeeded or waiting.*1Failed/);

    fireEvent.change(screen.getByLabelText("Period"), { target: { value: "90" } });
    fireEvent.change(screen.getByLabelText("Flow"), { target: { value: "support" } });
    fireEvent.change(screen.getByLabelText("Environment"), { target: { value: "production" } });
    await waitFor(() => expect(mocked.dashboard).toHaveBeenLastCalledWith(90, "support", "production"));
  });
});

describe("Settings → Tracing", () => {
  it("saves OpenTelemetry and LangSmith settings and sends a test span", async () => {
    mocked.tracing.mockResolvedValue({
      otlp: { enabled: false, endpoint: "", headers: {}, service_name: "easychain" },
      langsmith: { enabled: false, project: "easychain", endpoint: "https://api.smith.langchain.com" },
      langsmith_key: false,
    });
    mocked.saveTracing.mockResolvedValue({});
    mocked.testTracing.mockResolvedValue({ ok: true });
    render(<TracingSection />);
    expect((await screen.findByTestId("langsmith-key")).textContent).toContain("Add your LangSmith API key as the secret LANGSMITH_API_KEY");
    fireEvent.click(screen.getByRole("switch", { name: "Send runs to an OpenTelemetry collector" }));
    fireEvent.change(screen.getByPlaceholderText("http://collector:4318"), { target: { value: "http://tempo:4318" } });
    fireEvent.click(screen.getByRole("button", { name: "Add a header" }));
    fireEvent.change(screen.getByLabelText("Header name 1"), { target: { value: "x-api-key" } });
    fireEvent.change(screen.getByLabelText("Header value 1"), { target: { value: "{secret:TEMPO_KEY}" } });
    fireEvent.click(screen.getByRole("switch", { name: "Trace runs in LangSmith" }));
    fireEvent.click(screen.getByRole("button", { name: "Save tracing" }));
    await waitFor(() =>
      expect(mocked.saveTracing).toHaveBeenCalledWith({
        otlp: { enabled: true, endpoint: "http://tempo:4318", headers: { "x-api-key": "{secret:TEMPO_KEY}" }, service_name: "easychain" },
        langsmith: { enabled: true, project: "easychain", endpoint: "https://api.smith.langchain.com" },
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Send a test span" }));
    expect((await screen.findByRole("status")).textContent).toBe("The collector took it.");
  });
});
