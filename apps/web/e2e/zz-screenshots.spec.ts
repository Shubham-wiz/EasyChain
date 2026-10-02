// Screenshots for the docs. Skipped unless SCREENSHOTS=1:
//   SCREENSHOTS=1 pnpm --filter @easychain/web exec playwright test e2e/zz-screenshots.spec.ts

import { expect, test } from "@playwright/test";
import { resolve } from "node:path";
import { step } from "./helpers";

const out = (name: string) => resolve(process.cwd(), "../../docs/images", name);

test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 to refresh the docs images");

test("Phase 2 screenshots", async ({ page, request }) => {
  const created = await (await request.post("/api/flows", { data: { template: "approve-reply" } })).json();
  await page.goto(`/#/flows/${created.id}`);
  await expect(step(page, "review")).toBeVisible();
  await page.getByTestId("open-run").click();
  await page.getByRole("switch", { name: "Use the stand-in AI" }).click();
  await page.getByLabel("email", { exact: true }).fill("Hi, my order 1234 hasn't arrived yet. Can you check where it is?");
  await page.getByTestId("run-submit").click();
  await expect(page.getByTestId("run-waiting")).toBeVisible();
  await page.screenshot({ path: out("ask-a-human.png") });

  await page.getByTestId("run-waiting").getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("run-output")).toBeVisible();
  await page.getByRole("button", { name: /Save Points/ }).click();
  await page.getByTestId("save-points").getByRole("button", { name: /Before Check the reply/ }).first().click();
  await expect(page.getByTestId("save-point-detail")).toBeVisible();
  await page.getByTestId("save-point-detail").scrollIntoViewIfNeeded();
  await page.screenshot({ path: out("save-points.png") });

  const many = await (await request.post("/api/flows", { data: { template: "summarise-many-pages" } })).json();
  await page.goto(`/#/flows/${many.id}`);
  await expect(step(page, "each_page")).toBeVisible();
  await page.getByTestId("open-run").click();
  await page.getByLabel("urls", { exact: true }).fill(JSON.stringify(["http://127.0.0.1:8124/pages/bees", "http://127.0.0.1:8124/pages/coffee", "http://127.0.0.1:8124/pages/chess"]));
  await page.getByTestId("run-submit").click();
  await expect(page.getByTestId("run-output")).toBeVisible();
  await page.screenshot({ path: out("for-each.png") });

  for (const draft of ["Thanks for the quick reply!", "Can I change my delivery address?"]) {
    await request.post("/api/runs", { data: { flow_id: created.id, inputs: { email: draft }, stand_in: true, background: true } });
  }
  await page.waitForTimeout(1500);
  await page.goto("/#/inbox");
  await expect(page.getByTestId("inbox-item").first()).toBeVisible();
  await page.screenshot({ path: out("inbox.png") });
});

test("Phase 3 screenshots", async ({ page, request }) => {
  const analyst = await (await request.post("/api/flows", { data: { template: "sql-analyst" } })).json();
  await page.goto(`/#/flows/${analyst.id}`);
  const agent = page.locator('[data-testid^="step-"]').filter({ has: page.locator(".tools-handle") }).first();
  await expect(agent).toBeVisible();
  await agent.click({ position: { x: 60, y: 14 } });
  await page.getByRole("tab", { name: "Inspect" }).click();
  await expect(page.getByTestId("agent-tools")).toBeVisible();
  await page.screenshot({ path: out("agent.png") });

  const bot = await (await request.post("/api/flows", { data: { template: "support-bot" } })).json();
  await page.goto(`/#/flows/${bot.id}`);
  await expect(step(page, "search")).toBeVisible();
  await page.getByTestId("open-run").click();
  await page.getByTestId("chat-input").fill("How long do refunds take?");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("chat-log").getByRole("link", { name: "Source 1" }).first()).toBeVisible();
  await page.screenshot({ path: out("support-bot.png") });

  await page.goto("/#/knowledge/help_centre");
  const search = page.getByRole("region", { name: "Try a search" });
  await search.getByLabel("Question").fill("Can I cancel my subscription?");
  await search.getByRole("button", { name: "Search" }).click();
  await expect(search.locator("li").first()).toBeVisible();
  await page.screenshot({ path: out("knowledge.png"), fullPage: true });
});
