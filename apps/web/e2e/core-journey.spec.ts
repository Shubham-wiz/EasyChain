// The Phase 1 acceptance journey: a new user builds a summarise-this-URL flow from a
// blank canvas, runs it, debugs a failure, and exports Python that runs unchanged.

import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { connect, dragFromLibrary, expectNoProblems, FAKE_URL, inspect, setField, step } from "./helpers";

test("build, run, debug and export a summarise-this-URL flow", async ({ page }) => {
  const started = Date.now();
  await page.goto("/");
  await page.getByTestId("new-flow").click();
  await expect(step(page, "input")).toBeVisible();
  await expect(step(page, "output")).toBeVisible();

  // Input: one field called url.
  await inspect(page, "input");
  await setField(page, "Field name", "url");
  await expect(step(page, "input")).toContainText("url");

  // Drag three steps from the library onto the canvas.
  await dragFromLibrary(page, "http_request", 260, 130);
  await expect(step(page, "fetch_the_page")).toBeVisible();
  await setField(page, "URL", "{url}");
  await setField(page, "Save the result as", "page");

  await dragFromLibrary(page, "instructions", 260, 640);
  await expect(step(page, "instructions")).toBeVisible();
  await page.getByLabel("Message", { exact: true }).fill("Summarise this page in three bullet points.\n\n{page}");

  await dragFromLibrary(page, "ai_model", 560, 640);
  await expect(step(page, "ai_model")).toBeVisible();
  await setField(page, "Save the reply as", "summary");

  // Wire them up: Input → Fetch → Instructions → AI Model → Output.
  await connect(page, "input", "fetch_the_page");
  await connect(page, "fetch_the_page", "instructions");
  await connect(page, "instructions", "ai_model");
  await connect(page, "ai_model", "output");
  await expect(page.locator(".react-flow__edge")).toHaveCount(4);

  // Output returns the summary.
  await inspect(page, "output");
  await page.getByRole("checkbox", { name: "summary" }).check();
  await expectNoProblems(page);
  await expect(page.getByTestId("save-state")).toHaveText("Saved", { timeout: 10_000 });

  // Run it and watch each step finish.
  await page.getByTestId("open-run").click();
  await page.getByLabel("url").fill(`${FAKE_URL}/pages/langchain`);
  await page.getByTestId("run-submit").click();
  const output = page.getByTestId("run-output");
  await expect(output).toContainText("[fake gpt-4o-mini]");
  await expect(output).toContainText("LangChain is an open-source framework");
  for (const id of ["fetch_the_page", "instructions", "ai_model"]) {
    await expect(step(page, id)).toHaveAttribute("data-status", "done");
  }
  await expect(page.getByTestId("badges-ai_model")).toContainText("tok");
  const buildAndRunSeconds = (Date.now() - started) / 1000;
  console.log(`Built and ran the flow in ${buildAndRunSeconds.toFixed(1)} s`);
  expect(buildAndRunSeconds).toBeLessThan(300);

  // Debug: a missing page fails on the web request, in plain words, pinned to that step.
  await page.getByLabel("url").fill(`${FAKE_URL}/pages/missing`);
  await page.getByTestId("run-submit").click();
  await expect(page.getByTestId("run-error")).toContainText("404 Not Found");
  await expect(page.getByTestId("run-error")).toContainText("Fetch the page failed");
  await expect(step(page, "fetch_the_page")).toHaveAttribute("data-status", "error");

  // Export the project and run it with plain Python: no Easy Chain needed.
  await page.getByTestId("export-button").click();
  await expect(page.getByTestId("code-view")).toContainText("StateGraph", { timeout: 20_000 });
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-zip").click()]);
  expect(download.suggestedFilename()).toBe("my_flow.zip");
  const dir = mkdtempSync(join(tmpdir(), "easychain-export-"));
  const zip = join(dir, "flow.zip");
  await download.saveAs(zip);
  const python = ["run", "--project", resolve("../../python"), "python"];
  execFileSync("uv", [...python, "-c", `import zipfile; zipfile.ZipFile(${JSON.stringify(zip)}).extractall(${JSON.stringify(dir)})`]);
  const project = join(dir, "my_flow");
  expect(readdirSync(project).sort()).toEqual([".env.example", "README.md", "langgraph.json", "my_flow.flow.yaml", "my_flow.py", "requirements.txt"]);
  const hideEasyChain = "import sys, runpy; sys.modules['easychain'] = None; sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')";
  const stdout = execFileSync("uv", [...python, "-c", hideEasyChain, "my_flow.py", JSON.stringify({ url: `${FAKE_URL}/pages/langchain` })], {
    cwd: project,
    env: { ...process.env, OPENAI_API_KEY: "sk-export-test", OPENAI_BASE_URL: `${FAKE_URL}/v1` },
  }).toString();
  const result = JSON.parse(stdout);
  expect(result.summary).toContain("[fake gpt-4o-mini]");
  expect(result.summary).toContain("LangChain");
});
