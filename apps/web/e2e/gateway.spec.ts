import { expect, test } from "@playwright/test";

// The model gateway (Phase 5.5): a budget set in Settings stops runs once it's used up, the run
// says so with a way to the settings, and the spending page shows what each flow cost.
const ASK = {
  name: "Gateway journey",
  steps: [
    { id: "input", type: "input", name: "Input", settings: { fields: [{ name: "question" }] } },
    { id: "answer_it", type: "ai_model", name: "Answer it", settings: { model: "openai:gpt-4o-mini", prompt: "question", save_as: "answer" } },
    { id: "output", type: "output", name: "Output", settings: { fields: ["answer"] } },
  ],
  connections: [
    { from: "input", to: "answer_it" },
    { from: "answer_it", to: "output" },
  ],
  canvas: { steps: { input: { x: 0, y: 120 }, answer_it: { x: 300, y: 160 }, output: { x: 600, y: 120 } }, notes: [] },
};

test.afterEach(async ({ request }) => {
  // The server is shared by the other journeys: leave no budget behind.
  await request.put("/api/gateway", { data: {} });
});

test("a budget stops runs when it's used up, and the spending page shows the cost", async ({ page, request }) => {
  const made = await request.post("/api/flows", { data: { spec: ASK } });
  const { id } = await made.json();
  // A price that makes each call cost about a dollar (no real money: the fake model server).
  await request.put("/api/gateway", { data: { prices: { "openai:gpt-4o-mini": { input: 100_000, output: 0 } } } });

  await page.goto(`/#/flows/${id}`);
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("tab", { name: "Model gateway", exact: true }).click();
  await page.getByLabel("Drafts budget in dollars", { exact: true }).fill("0.5");
  await page.getByLabel("Drafts budget period", { exact: true }).selectOption("day");
  await page.getByRole("button", { name: "Save the model gateway", exact: true }).click();
  await expect(page.getByRole("button", { name: "Saved", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");

  // The first run is allowed (nothing spent yet) and uses the budget up.
  await page.getByTestId("open-run").click();
  const standIn = page.getByRole("switch", { name: "Use the stand-in AI" });
  if ((await standIn.getAttribute("aria-checked")) === "true") await standIn.click();
  await page.getByLabel("question", { exact: true }).fill("What is LangGraph, and what is it for?");
  await page.getByTestId("run-submit").click();
  await expect(page.getByTestId("run-output")).toBeVisible();

  // The next one is stopped before the model is asked.
  await page.getByTestId("run-submit").click();
  const failed = page.getByTestId("run-error");
  await expect(failed).toContainText("The Drafts budget is used up");
  await failed.getByRole("button", { name: "Model gateway settings", exact: true }).click();
  await expect(page.getByRole("tab", { name: "Model gateway", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("budget-drafts-spent")).toContainText("of $0.50 today");
  await expect(page.getByTestId("budget-alerts")).toContainText("Drafts: used up");
  const spending = page.getByTestId("spending");
  await spending.getByLabel("Spending period", { exact: true }).selectOption("today");
  await expect(spending.getByRole("row", { name: /Gateway journey/ })).toContainText("$1.00");
  await spending.getByLabel("Spending by", { exact: true }).selectOption("environment");
  await expect(spending.getByRole("row", { name: /Drafts/ })).toBeVisible();
});
