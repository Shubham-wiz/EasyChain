// Phase 4 journeys: the describe-it copilot.

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { FAKE_URL, step } from "./helpers";

type Spec = Record<string, unknown>;

async function createFlow(request: APIRequestContext, spec: Spec): Promise<string> {
  const res = await request.post("/api/flows", { data: { spec } });
  expect(res.ok()).toBeTruthy();
  return (await res.json()).id;
}

async function openFlow(page: Page, id: string) {
  await page.goto(`/#/flows/${id}`);
  await expect(step(page, "input")).toBeVisible();
}

async function axe(page: Page, include?: string) {
  let builder = new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).exclude(".react-flow__minimap");
  if (include) builder = builder.include(include);
  const serious = (await builder.analyze()).violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  for (const v of serious) console.log(`${v.id}: ${v.help}\n  ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join("\n  ")}`);
  return serious;
}

const echo: Spec = {
  name: "Summarise",
  steps: [
    { id: "input", type: "input", name: "Input", settings: { fields: [{ name: "text" }] } },
    { id: "output", type: "output", name: "Output", settings: { fields: ["text"] } },
  ],
  connections: [{ from: "input", to: "output" }],
  canvas: { steps: { input: { x: 0, y: 100 }, output: { x: 320, y: 100 } }, notes: [] },
};

// What the copilot's model writes back: the whole flow, a summary and test cases.
const reply = `Here is the flow.

\`\`\`yaml
summary: Sums up the text in three bullet points.
flow:
  name: Summarise
  data:
  - {name: summary, description: Three bullet points}
  steps:
  - {id: input, type: input, name: Input, settings: {fields: [{name: text}]}}
  - {id: ask, type: instructions, name: Ask for a summary, settings: {user: "Sum up in three bullet points: {text}"}}
  - {id: summarise, type: ai_model, name: Summarise, settings: {save_as: summary}}
  - {id: output, type: output, name: Output, settings: {fields: [summary]}}
  connections:
  - {from: input, to: ask}
  - {from: ask, to: summarise}
  - {from: summarise, to: output}
tests:
- name: A short text
  inputs: {text: Bees live in colonies and make honey.}
  expect: {output.summary: {not_empty: true}}
\`\`\`
`;

test("Copilot: describe a change, review it on the canvas, apply it as one undo step", async ({ page, request }) => {
  const id = await createFlow(request, echo);
  await request.post(`${FAKE_URL}/__script`, { data: [reply] });
  await openFlow(page, id);

  await page.getByRole("tab", { name: "Copilot", exact: true }).click();
  await expect(page.getByTestId("copilot-model")).toContainText("gpt-4.1");
  await page.getByLabel("Describe the flow").fill("Sum the text up in three bullet points");
  await page.getByTestId("copilot-propose").click();

  const proposal = page.getByTestId("copilot-proposal");
  await expect(proposal).toContainText("Sums up the text in three bullet points.");
  await expect(proposal).toContainText("Add the AI Model step “Summarise”");
  await expect(page.getByTestId("copilot-preview-banner")).toBeVisible();
  await expect(page.getByTestId("copilot-mark-summarise")).toHaveText("New");
  await expect(page.getByTestId("copilot-mark-output")).toHaveText("Changed");
  expect(await axe(page, '[data-testid="copilot-panel"]')).toEqual([]);

  await page.getByTestId("copilot-accept").click();
  await expect(page.getByTestId("copilot-preview-banner")).toBeHidden();
  await expect(step(page, "summarise")).toBeVisible();
  await expect(page.getByTestId("copilot-mark-summarise")).toHaveCount(0);
  await expect(page.getByTestId("problems-button")).toContainText("No problems");
  await expect
    .poll(async () => (await (await request.get(`/api/flows/${id}`)).json()).spec.steps.map((s: { id: string }) => s.id).sort())
    .toEqual(["ask", "input", "output", "summarise"]);

  // The kept test case went into the flow's Test Set (adding no cases reports how many there are).
  await expect.poll(async () => (await (await request.post(`/api/flows/${id}/tests`, { data: { cases: [] } })).json()).total).toBe(1);

  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(step(page, "summarise")).toHaveCount(0);
  await expect(step(page, "output")).toBeVisible();
});

test("Copilot: discarding a proposal leaves the flow as it was", async ({ page, request }) => {
  const id = await createFlow(request, echo);
  await request.post(`${FAKE_URL}/__script`, { data: [reply] });
  await openFlow(page, id);
  await page.getByRole("tab", { name: "Copilot", exact: true }).click();
  await page.getByLabel("Describe the flow").fill("Sum the text up");
  await page.getByTestId("copilot-propose").click();
  await expect(step(page, "summarise")).toBeVisible();
  await page.getByTestId("copilot-discard").click();
  await expect(step(page, "summarise")).toHaveCount(0);
  await expect(page.getByTestId("copilot-preview-banner")).toBeHidden();
  await expect(page.getByRole("button", { name: "Undo", exact: true })).toBeDisabled();
});
