// Sign-in (Phase 5.6): the first Owner with the code from the server's log, adding people,
// keys for scripts, signing out and in, and signing in again over the page when a session ends.
// Runs against the second server in playwright.config.ts, which has sign-in on.

import AxeBuilder from "@axe-core/playwright";
import { execFileSync } from "node:child_process";
import { expect, test, type Page } from "@playwright/test";

const BASE = `http://127.0.0.1:${process.env.E2E_AUTH_PORT ?? 8126}`;
const PASSWORD = "correct horse battery";

test.use({ baseURL: BASE });

/** What `easychain users setup-code` prints: a fresh code for the first account. */
function setupCode(): string {
  const out = execFileSync("uv", ["run", "--project", "../../python", "easychain", "users", "setup-code", "--home", process.env.E2E_AUTH_HOME!], {
    encoding: "utf-8",
  });
  return out.trim();
}

async function serious(page: Page) {
  const found = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  return found.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id);
}

async function account(page: Page) {
  await page.getByRole("button", { name: "Your account" }).click();
}

async function signIn(page: Page, email: string, password = PASSWORD) {
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

test("sign-in: the first Owner, people, keys, and signing in again", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Make the first account" })).toBeVisible();
  expect(await serious(page)).toEqual([]);
  expect((await request.get("/api/flows")).status()).toBe(401);

  // The first account needs the code from the server's log.
  await page.getByLabel("Code from the server's log").fill("AAAA-AAAA-AAAA");
  await page.getByLabel("Your name").fill("Olive");
  await page.getByLabel("Email", { exact: true }).fill("olive@example.com");
  await page.getByLabel("Password (at least 10 characters)").fill(PASSWORD);
  await page.getByRole("button", { name: "Make the account" }).click();
  await expect(page.getByRole("alert")).toContainText("doesn't match");
  await page.getByLabel("Code from the server's log").fill(setupCode());
  await page.getByRole("button", { name: "Make the account" }).click();
  await expect(page.getByRole("heading", { name: /Draw your AI app/ })).toBeVisible();

  // The Owner adds a Runner.
  await account(page);
  await expect(page.getByText("Owner", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "People" }).click();
  const people = page.getByRole("region", { name: "People" });
  await people.getByLabel("Their email").fill("rita@example.com");
  await people.getByLabel("Their name").fill("Rita");
  await people.getByLabel("Their first password").fill(PASSWORD);
  await people.getByLabel("Their role").selectOption("runner");
  await people.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByTestId("person-rita@example.com")).toContainText("hasn't signed in yet");
  await expect(page.getByLabel("Role of rita@example.com")).toHaveValue("runner");

  // A key for scripts, shown once; a Runner key can read and run, not change flows.
  await page.getByRole("tab", { name: "Keys for scripts" }).click();
  await page.getByLabel("What the key is for").fill("Support site");
  await page.getByLabel("The key's role").selectOption("runner");
  await page.getByRole("button", { name: "Make a key" }).click();
  const key = await page.getByLabel("The new key").inputValue();
  expect(key).toMatch(/^eck_/);
  const headers = { Authorization: `Bearer ${key}` };
  expect((await page.request.get("/api/flows", { headers })).status()).toBe(200);
  const made = await page.request.post("/api/flows", { headers, data: { template: "hello" } });
  expect(made.status()).toBe(403);
  expect((await made.json()).message).toContain("needs Editor or above");
  await page.keyboard.press("Escape");

  // Signing out starts afresh at the sign-in page.
  await account(page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to Easy Chain" })).toBeVisible();
  await signIn(page, "rita@example.com", "not the password");
  await expect(page.getByRole("alert")).toContainText("don't match an account");
  expect(await serious(page)).toEqual([]);
  await signIn(page, "rita@example.com");
  await expect(page.getByRole("heading", { name: /Draw your AI app/ })).toBeVisible();
  await account(page);
  await expect(page.getByText("Runner", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "People" })).toHaveCount(0);
  await page.keyboard.press("Escape");

  // The session ends while the page is open: sign in again over it, and carry on.
  await page.context().clearCookies();
  await page.getByTestId("inbox-link").click();
  await expect(page.getByRole("main", { name: "Sign in again" })).toBeVisible();
  await signIn(page, "rita@example.com");
  await expect(page.getByRole("main", { name: "Sign in again" })).toHaveCount(0);
  await expect(page).toHaveURL(/#\/inbox/);
});
