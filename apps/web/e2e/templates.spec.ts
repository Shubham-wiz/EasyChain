import { expect, test } from "@playwright/test";
import { step } from "./helpers";

test("Try it: chat assistant keeps the conversation", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-chat-assistant").getByRole("button", { name: "Try it" }).click();
  const log = page.getByTestId("chat-log");
  await expect(log).toContainText("[fake gpt-4o-mini]");
  // Screen readers hear the finished reply, not each streamed token.
  await expect(log).not.toHaveAttribute("aria-live");
  await expect(page.getByTestId("run-announcer")).toContainText("Reply: [fake gpt-4o-mini]");
  await page.getByTestId("chat-input").fill("And a second message");
  await page.keyboard.press("Enter");
  await expect(log.getByText("And a second message", { exact: true })).toBeVisible();
  await expect(log.locator("text=[fake gpt-4o-mini]")).toHaveCount(2);
  await expect(step(page, "reply")).toHaveAttribute("data-status", "done");
  await page.getByRole("button", { name: "New conversation" }).click();
  await expect(log).toContainText("Say hello");
});

test("Try it: a Decision lights up the exit it took, and runs replay", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-reply-to-feedback").getByRole("button", { name: "Try it" }).click();
  await expect(page.getByTestId("run-output")).toContainText("Complaint");
  await expect(step(page, "what_kind")).toHaveAttribute("data-status", "done");
  await expect(step(page, "apologise")).toHaveAttribute("data-status", "done");
  await expect(step(page, "thank")).toHaveAttribute("data-status", "idle");
  await expect(page.getByTestId("badges-what_kind")).toContainText("Complaint");

  // Run Replay plays the recorded run back on the canvas.
  await page.getByRole("button", { name: "Recent runs" }).click();
  await page.getByRole("button", { name: "Replay" }).first().click();
  await expect(step(page, "draft_reply")).toHaveAttribute("data-status", "done", { timeout: 20_000 });
});

test("Try it runs once: reload, Back and Forward don't start another run", async ({ page, request }) => {
  await page.goto("/");
  await page.getByTestId("template-reply-to-feedback").getByRole("button", { name: "Try it" }).click();
  await expect(page.getByTestId("run-output")).toContainText("Complaint");
  expect(page.url()).not.toContain("try=1");
  const flowId = /#\/flows\/([a-z0-9-]+)/.exec(page.url())![1];

  await page.reload();
  await expect(step(page, "what_kind")).toBeVisible();
  // Opened as a plain flow: the Inspect tab, not a new run.
  await expect(page.getByRole("tab", { name: "Inspect" })).toHaveAttribute("aria-selected", "true");
  await page.goBack();
  await expect(page.getByTestId("template-reply-to-feedback")).toBeVisible();
  await page.goForward();
  await expect(step(page, "what_kind")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Inspect" })).toHaveAttribute("aria-selected", "true");
  await page.waitForLoadState("networkidle");
  const runs = await (await request.get(`/api/runs?flow_id=${flowId}`)).json();
  expect(runs).toHaveLength(1);
});

test("a dropped event stream picks the run up again", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-reply-to-feedback").getByRole("button", { name: "Use template" }).click();
  await expect(step(page, "what_kind")).toBeVisible();
  // The connection drops after the run's first few events.
  await page.route("**/api/runs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    const response = await route.fetch();
    const events = (await response.text()).split("\n\n").filter(Boolean);
    expect(events.length).toBeGreaterThan(4);
    await route.fulfill({ response, body: `${events.slice(0, 3).join("\n\n")}\n\n` });
  });
  const followed = page.waitForRequest((r) => /\/api\/runs\/[a-f0-9]+\/events\?after=\d+/.test(r.url()));
  await page.getByTestId("open-run").click();
  await page.getByRole("button", { name: "Use examples" }).click();
  await page.getByTestId("run-submit").click();
  expect(new URL((await followed).url()).searchParams.get("after")).not.toBe("0");
  await expect(page.getByTestId("run-output")).toContainText("Complaint");
  await expect(page.getByTestId("run-summary")).toContainText("Finished");
  await expect(step(page, "apologise")).toHaveAttribute("data-status", "done");
});

test("the stand-in AI runs a flow without a key", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-summarise-url").getByRole("button", { name: "Use template" }).click();
  await page.getByTestId("open-run").click();
  await page.getByRole("switch", { name: "Use the stand-in AI" }).click();
  await page.getByLabel("url").fill(`http://127.0.0.1:${process.env.E2E_FAKE_PORT ?? 8124}/pages/bees`);
  await page.getByTestId("run-submit").click();
  await expect(page.getByTestId("run-output")).toContainText("[Stand-in AI");
  await expect(page.getByTestId("run-summary")).toContainText("stand-in AI");
  await page.getByRole("switch", { name: "Use the stand-in AI" }).click();
});
