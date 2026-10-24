import { expect, test } from "@playwright/test";

// Publish (Phase 5.4): a version goes to Development, a chat link gives people a chat page,
// and Staging waits for a passing Test Run.
test("publish a chat flow and talk to it on its chat page", async ({ page, context }) => {
  const made = await page.request.post("/api/flows", { data: { template: "chat-assistant", name: "Published chat" } });
  const { id } = await made.json();
  await page.goto(`/#/flows/${id}`);
  await page.getByTestId("publish-button").click();
  const dev = page.getByTestId("env-dev");
  await dev.getByRole("button", { name: "Publish to Development", exact: true }).click();
  await expect(dev.getByText(/^Version [0-9a-f]{7}/)).toBeVisible();

  // Staging needs a kept Test Run of this version, run in Staging.
  const staging = page.getByTestId("env-staging");
  await staging.getByRole("button", { name: "Publish to Staging", exact: true }).click();
  await expect(staging.getByRole("alert")).toContainText("needs a kept Test Run of this version, run in Staging");
  await expect(staging.getByRole("button", { name: "Open Tests", exact: true })).toBeVisible();

  // A chat link, and its page.
  await page.getByLabel("Kind", { exact: true }).selectOption("chat");
  await page.getByLabel("Name", { exact: true }).fill("Help page");
  await page.getByRole("button", { name: "Make the chat link", exact: true }).click();
  const link = await page.getByLabel("Chat page address", { exact: true }).inputValue();
  expect(link).toMatch(/\/chat\/[0-9a-f]+$/);
  await expect(page.getByLabel("Widget code", { exact: true })).toHaveValue(/widget\.js" data-chat="/);

  const chat = await context.newPage();
  await chat.goto(link);
  await expect(chat.getByText("Published chat", { exact: true })).toBeVisible();
  await chat.getByLabel("Your message", { exact: true }).fill("Hello, who are you?");
  await chat.getByRole("button", { name: "Send", exact: true }).click();
  await expect(chat.locator(".msg.them")).toHaveCount(1);
  await expect(chat.locator(".msg.them")).not.toBeEmpty();
});
