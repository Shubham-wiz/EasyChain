import { expect, test, type APIRequestContext } from "@playwright/test";
import { FAKE_URL } from "./helpers";

// Dashboards and tracing (Phase 5.7): runs show up on the dashboard per day and per flow, the
// charts read out a day, and Settings → Tracing saves and tests a collector.
const step = (model: string) => ({ id: "answer_it", type: "ai_model", name: "Answer it", settings: { model, prompt: "question", save_as: "answer" } });
const flow = (name: string, model: string) => ({
  name,
  steps: [
    { id: "input", type: "input", name: "Input", settings: { fields: [{ name: "question" }] } },
    step(model),
    { id: "output", type: "output", name: "Output", settings: { fields: ["answer"] } },
  ],
  connections: [
    { from: "input", to: "answer_it" },
    { from: "answer_it", to: "output" },
  ],
  canvas: { steps: { input: { x: 0, y: 120 }, answer_it: { x: 300, y: 160 }, output: { x: 600, y: 120 } }, notes: [] },
});

async function run(request: APIRequestContext, flowId: string, question: string) {
  const started = await request.post("/api/runs", { data: { flow_id: flowId, inputs: { question }, background: true } });
  const { run_id } = await started.json();
  await expect.poll(async () => (await (await request.get(`/api/runs/${run_id}`)).json()).status, { timeout: 30_000 }).toMatch(/^(ok|error)$/);
}

test.afterEach(async ({ request }) => {
  await request.put("/api/settings/tracing", { data: {} });
  await request.put("/api/gateway", { data: {} });
});

test("the dashboard shows runs, failures and cost, per flow, and tracing is set up in Settings", async ({ page, request }) => {
  const good = (await (await request.post("/api/flows", { data: { spec: flow("Dashboard journey", "openai:gpt-4o-mini") } })).json()).id;
  const bad = (await (await request.post("/api/flows", { data: { spec: flow("Dashboard broken", "openai:gpt-4o-down") } })).json()).id;
  // A price that makes each call cost about a dollar (the fake model server costs nothing).
  await request.put("/api/gateway", { data: { retries: 0, prices: { "openai:gpt-4o-mini": { input: 100_000, output: 0 } } } });
  await run(request, good, "What is LangGraph?");
  await run(request, good, "And LangChain?");
  await run(request, bad, "Hello?");

  await page.goto("/#/");
  await page.getByTestId("dashboard-link").click();
  await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
  await page.getByLabel("Period", { exact: true }).selectOption("7");
  const flows = page.getByTestId("dashboard-flows");
  await expect(flows.getByRole("row", { name: /Dashboard journey/ })).toBeVisible();
  await expect(page.getByTestId("dashboard-errors")).toContainText("Dashboard broken");

  // One flow: its own numbers.
  await page.getByLabel("Flow", { exact: true }).selectOption({ label: "Dashboard journey" });
  await expect(page.getByTestId("tile-runs")).toHaveText(/^Runs2/);
  await expect(page.getByTestId("tile-errors")).toContainText("0%");
  await expect(page.getByTestId("tile-cost")).not.toContainText("$0.00");
  const chart = page.getByLabel("Runs a day: use the arrow keys to read each day", { exact: true });
  await chart.focus();
  await chart.press("ArrowRight");
  await expect(page.getByRole("status")).toContainText("2");
  if (process.env.SCREENSHOTS) await page.screenshot({ path: "test-results/dashboard.png", fullPage: true });
  await page.getByLabel("Environment", { exact: true }).selectOption("production");
  await expect(page.getByTestId("tile-runs")).toHaveText(/^Runs0/);

  // Settings → Tracing.
  await page.goto("/#/");
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("tab", { name: "Tracing", exact: true }).click();
  await expect(page.getByTestId("langsmith-key")).toContainText("LANGSMITH_API_KEY");
  await page.getByRole("switch", { name: "Send runs to an OpenTelemetry collector" }).click();
  await page.getByPlaceholder("http://collector:4318").fill(FAKE_URL);
  await page.getByRole("button", { name: "Save tracing", exact: true }).click();
  await expect(page.getByRole("button", { name: "Saved", exact: true })).toBeVisible();
  // The fake model server isn't a collector: the test says so.
  await page.getByRole("button", { name: "Send a test span", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("The collector didn't take the span");
});
