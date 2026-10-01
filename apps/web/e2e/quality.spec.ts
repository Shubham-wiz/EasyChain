// Quality bar: the canvas stays responsive at 300+ steps, and pages pass automated
// WCAG 2.2 AA checks (axe-core). Automated checks don't replace a manual audit.

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { inspect, step } from "./helpers";

function bigFlow(n: number) {
  const steps: unknown[] = [{ id: "input", type: "input", name: "Input", settings: { fields: [{ name: "n", type: "number" }] } }];
  const connections: unknown[] = [];
  const canvas: Record<string, { x: number; y: number }> = { input: { x: 0, y: 0 } };
  let prev = "input";
  for (let i = 0; i < n; i++) {
    const id = `step_${i}`;
    steps.push({ id, type: "code", name: `Step ${i}`, settings: { code: "def run(data):\n    return {'n': data['n'] + 1}\n" } });
    connections.push({ from: prev, to: id });
    canvas[id] = { x: ((i + 1) % 20) * 260, y: Math.floor((i + 1) / 20) * 160 };
    prev = id;
  }
  steps.push({ id: "output", type: "output", name: "Output", settings: { fields: ["n"] } });
  connections.push({ from: prev, to: "output" });
  canvas.output = { x: 0, y: 20 * 160 };
  return { name: `Big flow ${n}`, steps, connections, canvas: { steps: canvas } };
}

test("the canvas stays responsive with 300 steps", async ({ page, request }) => {
  const created = await request.post("/api/flows", { data: { spec: bigFlow(300) } });
  const { id } = await created.json();
  const opened = Date.now();
  await page.goto(`/#/flows/${id}`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
  await expect(page.getByTestId("problems-button")).toContainText("No problems", { timeout: 20_000 });
  const openMs = Date.now() - opened;

  // Select, drag and undo a step: each interaction should feel instant.
  const node = page.locator(".react-flow__node").first();
  const box = (await node.boundingBox())!;
  const t0 = Date.now();
  await page.mouse.move(box.x + 40, box.y + 12);
  await page.mouse.down();
  await page.mouse.move(box.x + 140, box.y + 80, { steps: 10 });
  await page.mouse.up();
  const dragMs = Date.now() - t0;

  // Frame timing while panning across the canvas.
  const frames = await page.evaluate(async () => {
    const times: number[] = [];
    let last = performance.now();
    const pane = document.querySelector(".react-flow__pane")!;
    for (let i = 0; i < 30; i++) {
      pane.dispatchEvent(new WheelEvent("wheel", { deltaX: 40, deltaY: 0, bubbles: true }));
      await new Promise((r) => requestAnimationFrame(r));
      const now = performance.now();
      times.push(now - last);
      last = now;
    }
    times.sort((a, b) => a - b);
    return { median: times[Math.floor(times.length / 2)], p95: times[Math.floor(times.length * 0.95)] };
  });
  console.log(`300 steps: open ${openMs} ms, drag ${dragMs} ms, frame median ${frames.median.toFixed(1)} ms, p95 ${frames.p95.toFixed(1)} ms`);
  expect(openMs).toBeLessThan(15_000);
  expect(dragMs).toBeLessThan(2_000);
  expect(frames.median).toBeLessThan(50);
});

async function axe(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .exclude(".react-flow__minimap") // decorative overview, duplicated by the canvas itself
    .analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  for (const v of serious) console.log(`${v.id}: ${v.help}\n  ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join("\n  ")}`);
  return serious;
}

test("home and editor have no serious accessibility violations", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Start from a template")).toBeVisible();
  expect(await axe(page)).toEqual([]);

  await page.getByTestId("template-summarise-url").getByRole("button", { name: "Use template" }).click();
  await expect(step(page, "summarise")).toBeVisible();
  await inspect(page, "summarise");
  expect(await axe(page)).toEqual([]);

  await page.getByRole("button", { name: "Toggle theme" }).click();
  expect(await axe(page)).toEqual([]);
  await page.getByRole("button", { name: "Toggle theme" }).click();
});

test("the Inbox, a waiting run and the Flow Data panel have no serious accessibility violations", async ({ page, request }) => {
  const spec = {
    name: "Accessible approval",
    steps: [
      { id: "input", type: "input", name: "Input", settings: { fields: [{ name: "draft" }] } },
      { id: "review", type: "ask_human", name: "Review", settings: { kind: "choose", question: "Which tone?", options: ["Warm", "Formal"], show: ["draft"] } },
      { id: "output", type: "output", name: "Output", settings: { fields: ["human_answer"] } },
    ],
    connections: [
      { from: "input", to: "review" },
      { from: "review", to: "output", exit: "Warm" },
      { from: "review", to: "output", exit: "Formal" },
    ],
    canvas: { steps: { input: { x: 0, y: 100 }, review: { x: 300, y: 100 }, output: { x: 650, y: 100 } }, notes: [] },
  };
  const id = (await (await request.post("/api/flows", { data: { spec } })).json()).id;
  await request.post("/api/runs", { data: { flow_id: id, inputs: { draft: "Hello" }, background: true } });
  await expect.poll(async () => (await (await request.get("/api/inbox")).json()).length, { timeout: 15_000 }).toBeGreaterThan(0);
  await page.goto("/#/inbox");
  await expect(page.getByTestId("inbox-item").first()).toBeVisible();
  expect(await axe(page)).toEqual([]);

  await page.goto(`/#/flows/${id}`);
  await expect(step(page, "review")).toBeVisible();
  await expect(page.getByLabel("Most rounds of steps")).toBeVisible();
  expect(await axe(page)).toEqual([]);
  await page.getByTestId("open-run").click();
  await expect(page.getByTestId("run-waiting")).toBeVisible();
  expect(await axe(page)).toEqual([]);
  await page.getByRole("button", { name: "Toggle theme" }).click();
  expect(await axe(page)).toEqual([]);
  await page.getByRole("button", { name: "Toggle theme" }).click();
});
