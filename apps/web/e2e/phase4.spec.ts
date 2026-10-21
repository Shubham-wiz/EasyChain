// Phase 4 journeys: the Research assistant (a Deep Agent with Helpers, skills, a to-do list and
// files), the Skills library, Inbox triage (a supervisor with an Inbox pause), the sandbox
// settings, and the describe-it copilot.

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

const bees = `${FAKE_URL}/pages/bees`;

// The model's turns, in the order the calls happen: the lead's, then (after it hands out a task)
// the Helper's until it replies, then the lead's again.
const research = [
  { call: "write_todos", args: { todos: [{ content: "Read the page about bees", status: "in_progress" }, { content: "Write the report", status: "pending" }] } },
  { call: "task", args: { subagent_type: "researcher", description: `How do honey bees live? Read ${bees}` } },
  { call: "read_page", args: { url: bees } },
  `- Honey bees live in colonies of thousands. (Honey bees, ${bees})`,
  { call: "write_file", args: { file_path: "/report.md", content: `# How honey bees live\n\nA colony has thousands of bees [1].\n\n## Sources\n1. Honey bees, ${bees}` } },
  { call: "task", args: { subagent_type: "reviewer", description: "Check /report.md against the notes." } },
  "OK",
  "Honey bees live in colonies of thousands. The report is in /report.md.",
];

test("Research assistant: a Deep Agent plans, hands work to its Helpers and writes the report", async ({ page, request }) => {
  await page.goto("/");
  await page.getByTestId("template-research-assistant").getByRole("button", { name: "Use template" }).click();
  await expect(step(page, "lead")).toBeVisible();
  await expect(page.getByTestId("helper-of-researcher")).toContainText("Helper of Research lead");
  await expect(page.getByTestId("helper-of-reviewer")).toContainText("Helper of Research lead");
  await expect(page.getByTestId("tool-of-read_page")).toContainText("Tool of Researcher");
  await expect(page.locator('.react-flow__edge[data-id^="helper:"]')).toHaveCount(2);
  await expect(page.getByTestId("problems-button")).toContainText("No problems");

  await step(page, "lead").click({ position: { x: 60, y: 14 } });
  await page.getByRole("tab", { name: "Inspect", exact: true }).click();
  await expect(page.getByTestId("deep-agent-helpers")).toContainText("Researcher");
  await expect(page.getByTestId("deep-agent-helpers")).toContainText("Reviewer");
  await expect(page.getByTestId("step-skills")).toContainText("research-report");
  expect(await axe(page, "aside")).toEqual([]);

  await request.post(`${FAKE_URL}/__script`, { data: research });
  await page.getByTestId("open-run").click();
  await page.getByLabel("request", { exact: true }).fill(`How do honey bees live?\n${bees}`);
  await page.getByTestId("run-submit").click();
  await expect(page.getByTestId("run-output")).toContainText("/report.md", { timeout: 30_000 });
  await expect(step(page, "lead")).toHaveAttribute("data-status", "done");

  // The trace: the to-do list, each Helper's task with its own calls under it, and the files.
  const trace = page.getByRole("region", { name: "Run trace" });
  await trace.getByRole("button", { name: /Research lead/ }).click();
  await expect(page.getByTestId("trace-todos")).toContainText("Read the page about bees");
  const researcher = page.getByTestId("helper-task-researcher");
  await expect(researcher).toContainText("Helper researcher");
  await expect(researcher.getByRole("list", { name: "What researcher did" })).toContainText("read_page");
  await expect(page.getByTestId("helper-task-reviewer")).toBeVisible();
  await expect(page.getByTestId("trace-files")).toContainText("A colony has thousands of bees [1].");
  expect(await axe(page, "aside")).toEqual([]);
});

test("Skills: the library lists the built-in skills, and a new skill can be saved and deleted", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("skills-link").click();
  const list = page.getByTestId("skills");
  await expect(list).toContainText("research-report");
  await expect(list).toContainText("source-notes");
  expect(await axe(page)).toEqual([]);

  await list.getByRole("link", { name: /^research-report/ }).click();
  await expect(page.getByTestId("skill-editor")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save my copy" })).toBeVisible();

  await page.getByTestId("new-skill").click();
  await page.getByLabel("Name", { exact: true }).fill("Tone of voice");
  const body = page.getByLabel("Contents of SKILL.md");
  await expect(body).toHaveValue(/name: tone-of-voice/);
  await body.fill("---\nname: tone-of-voice\ndescription: How we write to customers. Use it when drafting a reply.\n---\n\nWarm, short, no jargon.");
  await page.getByTestId("save-skill").click();
  await expect(page).toHaveURL(/#\/skills\/tone-of-voice$/);
  await expect(list.getByRole("link", { name: /^tone-of-voice/ })).toBeVisible();
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "Delete" }).click();
  await expect(list).not.toContainText("tone-of-voice");
});

test("Inbox triage: the supervisor hands the email out, and an urgent one waits for a person", async ({ page, request }) => {
  await page.goto("/");
  await page.getByTestId("template-inbox-triage").getByRole("button", { name: "Use template" }).click();
  await expect(step(page, "supervisor")).toBeVisible();
  await expect(page.getByTestId("problems-button")).toContainText("No problems");
  await request.post(`${FAKE_URL}/__script`, {
    data: [
      { answer: { category: "billing", urgency: "urgent", summary: "Charged twice for order 5521." } },
      "Look up the sender",
      { call: "find_customer", args: { sender: "maria@example.com" } },
      "Maria, a member.",
      "Draft a reply",
      { call: "search_help", args: { search_for: "charged twice" } },
      "Hi Maria, we refund a second charge the same day [1].",
      "Done",
    ],
  });
  await page.getByTestId("open-run").click();
  await page.getByLabel("email", { exact: true }).fill("From: maria@example.com\nI was charged twice for order 5521.");
  await page.getByTestId("run-submit").click();

  const waiting = page.getByTestId("run-waiting");
  await expect(waiting).toContainText("Check the reply before it goes out", { timeout: 30_000 });
  await expect(step(page, "lookup")).toHaveAttribute("data-status", "done");
  await expect(step(page, "drafter")).toHaveAttribute("data-status", "done");
  await expect(step(page, "ticketer")).toHaveAttribute("data-status", "idle");
  await waiting.getByLabel("Edit draft").fill("Hi Maria, we're refunding the second charge today.");
  await waiting.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("run-output")).toContainText("refunding the second charge today");
  await expect(page.getByTestId("run-output")).toContainText("billing");
});

test("Settings: the sandbox section says whether Code steps run in containers", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("tab", { name: "Sandbox", exact: true }).click();
  await expect(page.getByTestId("sandbox-settings")).toBeVisible();
  await expect(page.getByTestId("sandbox-status").first()).toBeVisible();
  expect(await axe(page, '[role="dialog"]')).toEqual([]);
});
