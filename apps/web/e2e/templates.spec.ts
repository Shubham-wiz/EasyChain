import { expect, test } from "@playwright/test";
import { step } from "./helpers";

test("Try it: chat assistant keeps the conversation", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-chat-assistant").getByRole("button", { name: "Try it" }).click();
  const log = page.getByTestId("chat-log");
  await expect(log).toContainText("[fake gpt-4o-mini]");
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
