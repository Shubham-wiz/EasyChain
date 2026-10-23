import { expect, test } from "@playwright/test";

// Workspaces (Phase 5.6d): each has its own flows; switching starts the app afresh in the other.
test("make a workspace, work in it, and switch back", async ({ page }) => {
  await page.goto("/");
  const name = `Team ${Date.now().toString(36)}`;
  await page.getByTestId("workspace-switcher").click();
  await page.getByRole("button", { name: "Manage workspaces", exact: true }).click();
  const section = page.getByRole("region", { name: "Workspaces" });
  await section.getByLabel("New workspace name", { exact: true }).fill(name);
  await section.getByRole("button", { name: "Make it", exact: true }).click();
  await expect(section.getByRole("status")).toContainText(`Made ${name}.`);
  await section.getByRole("status").getByRole("button", { name: "Switch to it", exact: true }).click();

  // A new workspace starts empty.
  await expect(page.getByTestId("workspace-switcher")).toContainText(name);
  await expect(page.getByText("No flows yet. Start from a template or a blank canvas.")).toBeVisible();
  await page.getByTestId("new-flow").click();
  await expect(page.getByTestId("canvas")).toBeVisible();
  await page.goto("/");
  await expect(page.getByText("My flow", { exact: true }).first()).toBeVisible();

  // Back in the default workspace, that flow isn't there.
  await page.getByTestId("workspace-switcher").click();
  await page.getByRole("list", { name: "Your workspaces" }).getByRole("button", { name: /Default workspace/ }).click();
  await expect(page.getByTestId("workspace-switcher")).toContainText("Default workspace");
  const flows = await page.request.get("/api/flows");
  expect((await flows.json()).every((f: { name: string }) => f.name !== name)).toBe(true);
  const theirs = await page.request.get("/api/workspaces");
  const made = (await theirs.json()).workspaces.find((w: { name: string }) => w.name === name);
  const inside = await page.request.get("/api/flows", { headers: { "X-EasyChain-Workspace": made.id } });
  expect((await inside.json()).map((f: { name: string }) => f.name)).toEqual(["My flow"]);
});
