// Sign-in (Phase 5.6): the first Owner with the code from the server's log, adding people,
// keys for scripts, signing out and in, and signing in again over the page when a session ends.
// Runs against the second server in playwright.config.ts, which has sign-in on.

import AxeBuilder from "@axe-core/playwright";
import { execFileSync } from "node:child_process";
import { expect, test, type Browser, type Page } from "@playwright/test";
import { FAKE_URL } from "./helpers";

const BASE = `http://127.0.0.1:${process.env.E2E_AUTH_PORT ?? 8126}`;
const PASSWORD = "correct horse battery";

test.use({ baseURL: BASE });

/** `easychain …` against the sign-in server's database. */
function easychain(args: string[], input?: string): string {
  return execFileSync("uv", ["run", "--project", "../../python", "easychain", ...args, "--home", process.env.E2E_AUTH_HOME!], { encoding: "utf-8", input, stdio: ["pipe", "pipe", "pipe"] }).trim();
}

/** What `easychain users setup-code` prints: a fresh code for the first account. */
function setupCode(): string {
  return easychain(["users", "setup-code"]);
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

  // The audit log has all of it: who did what, and the refused change isn't there.
  await page.getByRole("tab", { name: "Audit log" }).click();
  const log = page.getByTestId("audit-entries");
  await expect(log).toContainText("Olive made the first account olive@example.com");
  await expect(log).toContainText("Olive added rita@example.com");
  await expect(log).toContainText("Olive made a key for scripts Support site");
  await expect(log).not.toContainText("made flow");
  await page.getByLabel("What changed").selectOption({ label: "People" });
  await expect(log.locator("li")).toHaveCount(1);
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

/** A person with a role, added on the command line, signed in in a browser of their own. */
async function person(browser: Browser, email: string, role: string): Promise<Page> {
  easychain(["users", "add", email, "--role", role, "--password-stdin"], `${PASSWORD}\n`);
  const page = await (await browser.newContext({ baseURL: BASE })).newPage();
  await page.goto("/");
  await signIn(page, email);
  await expect(page.getByRole("heading", { name: /Draw your AI app/ })).toBeVisible();
  return page;
}

const APPROVAL = {
  name: "Approve a reply",
  steps: [
    { id: "input", type: "input", name: "Input", settings: { fields: [{ name: "draft" }] } },
    { id: "review", type: "ask_human", name: "Check the reply", settings: { question: "Send this reply?", show: ["draft"] } },
    { id: "send", type: "code", name: "Send", settings: { code: "def run(data):\n    return {'sent': 'SENT: ' + data['draft']}\n" } },
    { id: "output", type: "output", name: "Output", settings: { fields: ["sent"] } },
  ],
  connections: [
    { from: "input", to: "review" },
    { from: "review", to: "send", exit: "Approved" },
    { from: "review", to: "output", exit: "Rejected" },
    { from: "send", to: "output" },
  ],
  canvas: { steps: { input: { x: 0, y: 120 }, review: { x: 300, y: 120 }, send: { x: 600, y: 120 }, output: { x: 900, y: 120 } }, notes: [] },
};

test("roles: a Viewer looks, a Runner runs and answers, an Editor sees settings it can't change", async ({ browser }) => {
  // An Owner of their own (the first test made one; this one doesn't depend on it) makes a flow.
  easychain(["users", "add", "oscar@example.com", "--role", "owner", "--password-stdin"], `${PASSWORD}\n`);
  const ownerKey = easychain(["keys", "create", "oscar@example.com", "--name", "e2e"]);
  const owner = await (await browser.newContext({ baseURL: BASE, extraHTTPHeaders: { Authorization: `Bearer ${ownerKey}` } })).request;
  const made = await owner.post("/api/flows", { data: { spec: APPROVAL } });
  expect(made.status()).toBe(201);
  const flowId = (await made.json()).id;

  // A Viewer: no new flows, a view-only editor, nothing to run, settings only for their keys.
  const viewer = await person(browser, "vera@example.com", "viewer");
  await expect(viewer.getByTestId("cant-add-flows")).toContainText("Your role (Viewer) can't add or remove flows");
  await expect(viewer.getByTestId("new-flow")).toHaveCount(0);
  await viewer.goto(`/#/flows/${flowId}`);
  await expect(viewer.getByTestId("view-only")).toContainText("needs Editor or above");
  await expect(viewer.getByTestId("library-ai_model")).toHaveCount(0);
  await expect(viewer.getByTestId("open-run")).toHaveCount(0);
  await expect(viewer.getByRole("tab", { name: "Copilot" })).toHaveCount(0);
  await viewer.getByTestId("step-review").click({ position: { x: 60, y: 14 } });
  const fields = viewer.getByTestId("inspector-read-only");
  await expect(fields).toBeAttached();
  await expect(fields.getByRole("textbox").first()).toBeDisabled();
  await viewer.getByRole("tab", { name: "Run" }).click();
  await expect(viewer.getByTestId("cant-run")).toContainText("needs Runner or above");
  await expect(viewer.getByTestId("run-submit")).toBeDisabled();
  await viewer.getByRole("button", { name: "Settings" }).click();
  await expect(viewer.getByRole("tab")).toHaveText(["Keys for scripts"]);
  await viewer.keyboard.press("Escape");

  // A Runner runs the saved flow from the editor (the app sends the flow's id, not its steps).
  const runner = await person(browser, "rui@example.com", "runner");
  await runner.goto(`/#/flows/${flowId}`);
  await expect(runner.getByTestId("view-only")).toBeVisible();
  await runner.getByTestId("open-run").click();
  await runner.getByLabel("draft").fill("Thanks for writing");
  await runner.getByTestId("run-submit").click();
  await expect(runner.getByTestId("run-waiting")).toContainText("Send this reply?");

  // The Viewer sees it waiting in the Inbox but can't answer it.
  await viewer.goto("/#/inbox");
  const seen = viewer.getByTestId("inbox-item").filter({ hasText: "Approve a reply" });
  await expect(seen).toContainText("Your role (Viewer) can't answer the Inbox");
  await expect(seen.getByRole("button", { name: "Approve" })).toBeDisabled();

  // The Runner answers it there, and the run finishes; the Inbox records who answered.
  await runner.goto("/#/inbox");
  const item = runner.getByTestId("inbox-item").filter({ hasText: "Approve a reply" });
  await item.getByRole("button", { name: "Approve" }).click();
  await expect(runner.getByRole("status")).toContainText("The run carries on");
  const answered = await (await owner.get("/api/inbox?status=answered")).json();
  expect(answered.find((i: { flow_name: string }) => i.flow_name === "Approve a reply")?.answered_by).toBe("rui@example.com");

  // An Editor changes flows, and sees the server's settings without being able to change them.
  const editor = await person(browser, "eddie@example.com", "editor");
  await editor.goto(`/#/flows/${flowId}`);
  await expect(editor.getByTestId("library-ai_model")).toBeVisible();
  await expect(editor.getByTestId("view-only")).toHaveCount(0);
  await editor.getByRole("button", { name: "Settings" }).click();
  await expect(editor.getByRole("tab")).toHaveText(["Keys and providers", "MCP servers", "Notifications", "Sandbox", "Network", "Keys for scripts"]);
  await expect(editor.getByRole("dialog").getByRole("note")).toContainText("Your role (Editor) can't change these; that needs Owner or above");
  await expect(editor.getByLabel("Secret name")).toBeDisabled();
  await editor.keyboard.press("Escape");

  // On a shared server, a Web request step can't reach an internal address until an Owner
  // allows it; the run says so, and the fix opens Settings → Network.
  const fetchFlow = {
    name: "Fetch inside",
    steps: [
      { id: "input", type: "input", name: "Input", settings: { fields: [{ name: "q" }] } },
      { id: "fetch", type: "http_request", name: "Fetch", settings: { url: `${FAKE_URL}/health`, response: "text", save_as: "page" } },
      { id: "output", type: "output", name: "Output", settings: { fields: ["page"] } },
    ],
    connections: [
      { from: "input", to: "fetch" },
      { from: "fetch", to: "output" },
    ],
    canvas: { steps: { input: { x: 0, y: 120 }, fetch: { x: 300, y: 120 }, output: { x: 600, y: 120 } }, notes: [] },
  };
  const fetchId = (await (await owner.post("/api/flows", { data: { spec: fetchFlow } })).json()).id;
  await editor.goto(`/#/flows/${fetchId}`);
  await editor.getByTestId("open-run").click();
  await editor.getByLabel("q").fill("x");
  await editor.getByTestId("run-submit").click();
  const failed = editor.getByTestId("run-error");
  await expect(failed).toContainText("Easy Chain didn't connect to 127.0.0.1");
  await failed.getByRole("button", { name: "Network settings" }).click();
  await expect(editor.getByRole("tab", { name: "Network" })).toHaveAttribute("aria-selected", "true");
  await expect(editor.getByRole("radio", { name: /Public sites/ })).toBeChecked();
  await editor.keyboard.press("Escape");
  const allowed = await owner.put("/api/settings/network", { data: { mode: "public", allow: ["127.0.0.1"] } });
  expect(allowed.status()).toBe(200);
  await editor.getByTestId("run-submit").click();
  await expect(editor.getByTestId("run-output")).toContainText("ok");
});
