import { expect, test, type Page } from "@playwright/test";
import { connect, inspect, setField, step } from "./helpers";

const mod = process.platform === "darwin" ? "Meta" : "Control";

async function newFlow(page: Page) {
  await page.goto("/");
  await page.getByTestId("new-flow").click();
  await expect(step(page, "input")).toBeVisible();
}

test("click to add, one-click fixes, undo and redo", async ({ page }) => {
  await newFlow(page);
  // Clicking a library item with a step selected adds it after that step, connected.
  await inspect(page, "input");
  await page.getByTestId("library-ai_model").click();
  await expect(step(page, "ai_model")).toBeVisible();
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);

  // With "question" as its input the AI Model has something to read; remove the field to break it.
  await inspect(page, "input");
  await page.getByRole("button", { name: "Remove field question" }).click();
  await inspect(page, "ai_model");
  const fix = page.getByRole("button", { name: "Add Instructions before it" });
  await expect(fix).toBeVisible();
  await fix.click();
  await expect(step(page, "instructions")).toBeVisible();
  await expect(page.locator(".react-flow__edge")).toHaveCount(2);

  // Undo removes the inserted step; redo brings it back.
  await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } });
  await page.keyboard.press(`${mod}+z`);
  await expect(step(page, "instructions")).toHaveCount(0);
  await page.keyboard.press(`${mod}+Shift+z`);
  await expect(step(page, "instructions")).toBeVisible();

  // Delete with the keyboard.
  await step(page, "instructions").click({ position: { x: 60, y: 14 } });
  await page.keyboard.press("Delete");
  await expect(step(page, "instructions")).toHaveCount(0);
});

test("misspelt variable gets a suggestion and a fix", async ({ page }) => {
  await newFlow(page);
  await inspect(page, "input");
  await setField(page, "Field name", "page");
  await page.getByTestId("library-instructions").click();
  await expect(step(page, "instructions")).toBeVisible();
  await page.getByLabel("Message", { exact: true }).fill("Summarise {pgae}");
  const fix = page.getByRole("button", { name: "Use `page`" });
  await expect(fix).toBeVisible();
  await expect(page.getByText("Did you mean `page`?").first()).toBeVisible();
  await fix.click();
  await expect(page.getByLabel("Message", { exact: true })).toHaveValue("Summarise {page}");
});

test("copy and paste steps, quick-add from a dangling connection", async ({ page }) => {
  await newFlow(page);
  await inspect(page, "input");
  await page.getByTestId("library-code").click();
  await expect(step(page, "code")).toBeVisible();
  await step(page, "code").click({ position: { x: 60, y: 14 } });
  await page.keyboard.press(`${mod}+c`);
  await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } });
  await page.keyboard.press(`${mod}+v`);
  await expect(step(page, "code_2")).toBeVisible();

  // Drop a connection on empty canvas and pick what to add.
  const handle = step(page, "code_2").locator(".react-flow__handle.source");
  const box = (await handle.boundingBox())!;
  await page.mouse.move(box.x + 6, box.y + 6);
  await page.mouse.down();
  await page.mouse.move(box.x + 200, box.y + 180, { steps: 10 });
  await page.mouse.up();
  await page.getByRole("menuitem", { name: "Decision" }).click();
  await expect(step(page, "decision")).toBeVisible();
  await expect(page.locator(".react-flow__edge")).toHaveCount(2);
});

test("an invalid connection explains why", async ({ page }) => {
  await newFlow(page);
  await connect(page, "input", "output");
  await expect(page.getByRole("status")).toContainText("Put at least one step between Input and Output");
  await expect(page.locator(".react-flow__edge")).toHaveCount(0);
});

test("pro mode, dark theme, code tab and settings", async ({ page }) => {
  await newFlow(page);
  await page.getByRole("button", { name: "pro", exact: true }).click();
  await expect(page.getByTestId("library-ai_model")).toContainText("Chat model");
  await inspect(page, "input");
  await page.getByTestId("library-ai_model").click();
  await expect(page.getByText("Step id")).toBeVisible();
  await page.getByRole("tab", { name: "Code" }).click();
  await expect(page.getByTestId("code-view")).toContainText("init_chat_model", { timeout: 20_000 });

  await page.getByRole("button", { name: "Toggle theme" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);

  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByLabel("Secret name").fill("MY_TOKEN");
  await page.getByLabel("Secret value").fill("tok-123456");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("MY_TOKEN");
  await expect(page.getByRole("dialog")).not.toContainText("tok-123456");
  await page.getByRole("button", { name: "Delete MY_TOKEN" }).click();
  await expect(page.getByRole("dialog")).not.toContainText("MY_TOKEN");
});
