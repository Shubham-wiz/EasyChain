// Phase 2 journeys: Ask a Human (in the run panel and the Inbox), For Each, breakpoints,
// Save Points and time travel, stopping a run, triggers, the Flow Data panel, Sub-flows
// and a run that survives a page reload.

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { inspect, step } from "./helpers";

type Spec = Record<string, unknown>;

const input = (...fields: (string | Record<string, unknown>)[]) => ({
  id: "input",
  type: "input",
  name: "Input",
  settings: { fields: fields.map((f) => (typeof f === "string" ? { name: f } : f)) },
});
const output = (...fields: string[]) => ({ id: "output", type: "output", name: "Output", settings: { fields } });
const code = (id: string, body: string, extra: Record<string, unknown> = {}) => ({
  id,
  type: "code",
  name: id,
  settings: { code: `def run(data):\n${body}\n` },
  ...extra,
});

function laidOut(spec: Spec): Spec {
  const steps = spec.steps as { id: string }[];
  return { ...spec, canvas: { steps: Object.fromEntries(steps.map((s, i) => [s.id, { x: i * 300, y: 120 + (i % 2) * 40 }])), notes: [] } };
}

async function createFlow(request: APIRequestContext, spec: Spec): Promise<string> {
  const res = await request.post("/api/flows", { data: { spec: laidOut(spec) } });
  expect(res.ok()).toBeTruthy();
  return (await res.json()).id;
}

async function openFlow(page: Page, id: string) {
  await page.goto(`/#/flows/${id}`);
  await expect(step(page, "input")).toBeVisible();
}

async function waitForRun(request: APIRequestContext, runId: string, statuses: string[]) {
  await expect
    .poll(async () => (await (await request.get(`/api/runs/${runId}`)).json()).status, { timeout: 20_000 })
    .toMatch(new RegExp(`^(${statuses.join("|")})$`));
}

const APPROVAL = {
  name: "Approve the reply",
  steps: [
    input("draft"),
    { id: "review", type: "ask_human", name: "Check the reply", settings: { kind: "edit", question: "Send this reply?", show: ["draft"], field: "draft" } },
    code("send", "    return {'sent': 'SENT: ' + data['draft']}"),
    output("sent", "human_answer"),
  ],
  connections: [
    { from: "input", to: "review" },
    { from: "review", to: "send", exit: "Approved" },
    { from: "review", to: "output", exit: "Rejected" },
    { from: "send", to: "output" },
  ],
};

test("Ask a Human: the run waits, a person edits and approves in the run panel", async ({ page, request }) => {
  const id = await createFlow(request, APPROVAL);
  await openFlow(page, id);
  await page.getByTestId("open-run").click();
  await page.getByLabel("draft").fill("Thanks for writing");
  await page.getByTestId("run-submit").click();
  const waiting = page.getByTestId("run-waiting");
  await expect(waiting).toContainText("Send this reply?");
  await expect(step(page, "review")).toHaveAttribute("data-status", "waiting");
  await waiting.getByLabel("Edit draft").fill("Thanks for writing to us!");
  await waiting.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("run-output")).toContainText("SENT: Thanks for writing to us!");
  await expect(step(page, "review")).toContainText("Approved");
});

test("Inbox: a run started by the API waits there and is answered later", async ({ page, request }) => {
  const id = await createFlow(request, { ...APPROVAL, name: "Inbox approval" });
  const res = await request.post("/api/runs", { data: { flow_id: id, inputs: { draft: "From the API" }, background: true } });
  const { run_id } = await res.json();
  await waitForRun(request, run_id, ["paused"]);
  await page.goto("/");
  await expect(page.getByTestId("inbox-count")).toHaveText(/\d+/);
  await page.getByTestId("inbox-link").click();
  const item = page.getByTestId("inbox-item").filter({ hasText: "Inbox approval" });
  await expect(item).toContainText("From the API");
  await item.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByRole("status")).toContainText("The run carries on");
  await waitForRun(request, run_id, ["ok"]);
  const run = await (await request.get(`/api/runs/${run_id}`)).json();
  expect(run.output).toEqual({ human_answer: "Rejected" });
});

test("For Each: every item runs, with progress, and results keep their order", async ({ page, request }) => {
  const id = await createFlow(request, {
    name: "Shout each",
    steps: [
      input({ name: "words", type: "list" }),
      { id: "each", type: "for_each", name: "For each word", settings: { items: "words", item_name: "word", save_as: "loud" } },
      code("shout", "    return {'one': data['word'].upper()}"),
      output("loud"),
    ],
    connections: [
      { from: "input", to: "each" },
      { from: "each", to: "shout", exit: "Each item" },
      { from: "each", to: "output", exit: "When done" },
    ],
  });
  await openFlow(page, id);
  await page.getByTestId("open-run").click();
  await page.getByLabel("words").fill('["red", "green", "blue"]');
  await page.getByTestId("run-submit").click();
  await expect(page.getByTestId("run-output")).toContainText('"RED"');
  await expect(page.getByTestId("run-output")).toContainText('"BLUE"');
  const text = await page.getByTestId("run-output").innerText();
  expect(text.indexOf("RED")).toBeLessThan(text.indexOf("GREEN"));
  expect(text.indexOf("GREEN")).toBeLessThan(text.indexOf("BLUE"));
  await expect(page.getByTestId("progress-each")).toContainText("3 of 3 done");
});

test("breakpoints and Save Points: pause, carry on, change the data and run again from there", async ({ page, request }) => {
  const id = await createFlow(request, {
    name: "Two steps",
    steps: [
      input("text"),
      code("shout", "    return {'loud': data['text'].upper()}"),
      code("exclaim", "    return {'final': data['loud'] + '!'}"),
      output("final"),
    ],
    connections: [
      { from: "input", to: "shout" },
      { from: "shout", to: "exclaim" },
      { from: "exclaim", to: "output" },
    ],
  });
  await openFlow(page, id);
  await inspect(page, "exclaim");
  await page.getByRole("button", { name: "More options" }).click();
  await page.getByRole("switch", { name: "Pause before this step" }).click();
  await expect(page.getByTestId("breakpoint-before-exclaim")).toBeVisible();

  await page.getByTestId("open-run").click();
  await page.getByLabel("text").fill("hey");
  await page.getByTestId("run-submit").click();
  await expect(page.getByTestId("run-breakpoint")).toContainText("before exclaim");
  await page.getByTestId("continue-run").click();
  await expect(page.getByTestId("run-output")).toContainText("HEY!");

  await page.getByRole("button", { name: /Save Points/ }).click();
  await page.getByTestId("save-points").getByRole("button", { name: /Before exclaim/ }).click();
  await page.getByLabel("Value of loud").fill("CHANGED");
  await page.getByTestId("rerun-from-here").click();
  // Running again from a Save Point starts there, past the breakpoint it was taken at.
  await expect(page.getByTestId("run-output")).toContainText("CHANGED!");
});

test("Stop ends a running run, and the run can carry on afterwards", async ({ page, request }) => {
  const id = await createFlow(request, {
    name: "Slow",
    steps: [input("x"), code("slow", "    import time\n    time.sleep(float(data['x']))\n    return {'y': 'done'}"), output("y")],
    connections: [
      { from: "input", to: "slow" },
      { from: "slow", to: "output" },
    ],
  });
  await openFlow(page, id);
  await page.getByTestId("open-run").click();
  await page.getByLabel("x").fill("2");
  await page.getByTestId("run-submit").click();
  await expect(step(page, "slow")).toHaveAttribute("data-status", "running");
  await page.getByRole("button", { name: "Stop" }).click();
  await expect(page.getByTestId("run-cancelled")).toBeVisible({ timeout: 5_000 });
  await page.getByTestId("run-cancelled").getByRole("button", { name: "Carry on" }).click();
  await expect(page.getByTestId("run-output")).toContainText("done", { timeout: 15_000 });
});

test("a run keeps going when the page reloads", async ({ page, request }) => {
  const id = await createFlow(request, {
    name: "Reload",
    steps: [input("x"), code("slow", "    import time\n    time.sleep(2)\n    return {'y': 'finished ' + data['x']}"), output("y")],
    connections: [
      { from: "input", to: "slow" },
      { from: "slow", to: "output" },
    ],
  });
  await openFlow(page, id);
  await page.getByTestId("open-run").click();
  await page.getByLabel("x").fill("ok");
  await page.getByTestId("run-submit").click();
  await expect(step(page, "slow")).toHaveAttribute("data-status", "running");
  await page.reload();
  await expect(page.getByTestId("run-output")).toContainText("finished ok", { timeout: 15_000 });
});

test("triggers: a webhook starts the flow", async ({ page, request }) => {
  const id = await createFlow(request, {
    name: "Hooked",
    steps: [input("question"), code("echo", "    return {'answer': 'You asked: ' + data['question']}"), output("answer")],
    connections: [
      { from: "input", to: "echo" },
      { from: "echo", to: "output" },
    ],
  });
  await openFlow(page, id);
  await page.getByRole("button", { name: "Triggers", exact: true }).click();
  await page.getByTestId("create-trigger").click();
  const url = await page.getByTestId("trigger-url").innerText();
  const trigger = (await (await request.get(`/api/triggers?flow_id=${id}`)).json())[0];
  const fired = await request.post(url, { data: { question: "anyone there?" }, headers: { "X-Easychain-Token": trigger.token } });
  expect(fired.status()).toBe(202);
  await waitForRun(request, (await fired.json()).run_id, ["ok"]);
  await page.keyboard.press("Escape");
  await page.getByTestId("open-run").click();
  await page.getByRole("button", { name: "Recent runs" }).click();
  await expect(page.getByText("[webhook]")).toBeVisible();
});

test("Flow Data panel: declare a field with an update rule and flow settings", async ({ page, request }) => {
  const id = await createFlow(request, {
    name: "Data",
    steps: [input("text"), code("tag", "    return {'tags': [data['text']]}"), output("tags")],
    connections: [
      { from: "input", to: "tag" },
      { from: "tag", to: "output" },
    ],
  });
  await openFlow(page, id);
  await page.getByRole("button", { name: "Declare tags" }).click();
  const field = page.getByTestId("data-field-tags");
  await field.getByLabel("Field type").selectOption("list");
  await field.getByLabel("Update rule").selectOption("append");
  await page.getByLabel("Most rounds of steps").fill("40");
  await expect(page.getByTestId("save-state")).toHaveText("Saved");
  const saved = (await (await request.get(`/api/flows/${id}`)).json()).spec;
  expect(saved.data).toEqual([expect.objectContaining({ name: "tags", type: "list", update: "append" })]);
  expect(saved.settings.max_steps).toBe(40);
});

test("Sub-flows: run another flow as a step and open it by double-clicking", async ({ page, request }) => {
  const child = await createFlow(request, {
    name: "Tidy text",
    steps: [input("text"), code("tidy", "    return {'tidied': data['text'].strip().capitalize()}"), output("tidied")],
    connections: [
      { from: "input", to: "tidy" },
      { from: "tidy", to: "output" },
    ],
  });
  const parent = await createFlow(request, {
    name: "Uses tidy",
    steps: [input("text"), { id: "sub", type: "subflow", name: "Tidy it", settings: { flow: child } }, output("tidied")],
    connections: [
      { from: "input", to: "sub" },
      { from: "sub", to: "output" },
    ],
  });
  await openFlow(page, parent);
  await page.getByTestId("open-run").click();
  await page.getByLabel("text").fill("   hello there  ");
  await page.getByTestId("run-submit").click();
  await expect(page.getByTestId("run-output")).toContainText("Hello there");
  await step(page, "sub").dblclick();
  await expect(page).toHaveURL(new RegExp(`#/flows/${child}$`));
  await expect(step(page, "tidy")).toBeVisible();
});
