import { expect, test } from "@playwright/test";

// Test Sets in the app (Phase 5.1): add a case, give it inputs and checks, run it, and see
// what passed and why something didn't.
const SHOUT = {
  name: "Shout",
  steps: [
    { id: "input", type: "input", settings: { fields: [{ name: "text" }] } },
    { id: "shout", type: "code", settings: { code: "def run(data):\n    return {'loud': data['text'].upper()}\n" } },
    { id: "output", type: "output", settings: { fields: ["loud"] } },
  ],
  connections: [
    { from: "input", to: "shout" },
    { from: "shout", to: "output" },
  ],
};

test("Test Sets: write a case, run it, read why it failed", async ({ page }) => {
  const made = await page.request.post("/api/flows", { data: { spec: SHOUT } });
  const { id } = await made.json();
  await page.goto(`/#/flows/${id}`);
  await page.getByRole("tab", { name: "Tests", exact: true }).click();
  const panel = page.getByTestId("tests-panel");
  await expect(panel.getByText("No cases yet.", { exact: false })).toBeVisible();

  await panel.getByRole("button", { name: "Add a case", exact: true }).click();
  await panel.getByLabel("Case name", { exact: true }).fill("Shouts hello");
  await panel.getByLabel("Input text", { exact: true }).fill("hello");
  await panel.getByLabel("Add a check", { exact: true }).selectOption("value");
  await panel.getByLabel("Which value", { exact: true }).fill("output.loud");
  await panel.getByLabel("Rule", { exact: true }).selectOption("equals");
  await panel.getByLabel("Expected", { exact: true }).fill("HELLO");
  await panel.getByRole("button", { name: "Save the Test Set", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Saved", exact: true })).toBeVisible();

  await panel.getByRole("button", { name: "Run all", exact: true }).click();
  await expect(panel.getByRole("status")).toHaveText("1 of 1 passed.");
  await expect(page.getByTestId("case-Shouts hello").getByText("Passed", { exact: true })).toBeVisible();

  // A check that can't pass says what was wanted and what came.
  await panel.getByLabel("Expected", { exact: true }).fill("HELLO!");
  await panel.getByRole("button", { name: "Run Shouts hello", exact: true }).click();
  await expect(page.getByTestId("result-Shouts hello")).toContainText("expected 'HELLO!', got 'HELLO'");
  await expect(page.getByTestId("case-Shouts hello").getByText("Failed", { exact: true })).toBeVisible();

  // It's kept next to the flow, as YAML.
  const saved = await (await page.request.get(`/api/flows/${id}/tests`)).json();
  expect(saved.cases[0]).toMatchObject({ name: "Shouts hello", inputs: { text: "hello" }, expect: { "output.loud": "HELLO!" } });
});
