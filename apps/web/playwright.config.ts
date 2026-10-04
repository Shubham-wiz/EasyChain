import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// The e2e suite runs the real app: the built web app served by `easychain dev`,
// with model calls going to the fake OpenAI-compatible server (no real key needed).
const port = Number(process.env.E2E_PORT ?? 8123);
const fakePort = Number(process.env.E2E_FAKE_PORT ?? 8124);
const mcpPort = Number(process.env.E2E_MCP_PORT ?? 8125);
const home = mkdtempSync(join(tmpdir(), "easychain-e2e-"));
const python = "uv run --project ../../python";

export const FAKE_URL = `http://127.0.0.1:${fakePort}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  // "github" shows each failure as an annotation on the CI run's page.
  reporter: process.env.CI ? [["list"], ["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    acceptDownloads: true,
  },
  webServer: [
    {
      command: `${python} python -m easychain.testing.fake_openai --port ${fakePort}`,
      url: `${FAKE_URL}/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      // A small MCP server (arithmetic and city facts) over streamable HTTP at /mcp.
      command: `${python} python -m easychain.testing.mcp_server --http ${mcpPort}`,
      port: mcpPort,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `${python} easychain dev --port ${port}`,
      url: `http://127.0.0.1:${port}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        EASYCHAIN_HOME: home,
        EASYCHAIN_WEB_DIST: join(process.cwd(), "dist"),
        OPENAI_API_KEY: "sk-e2e-test-key",
        OPENAI_BASE_URL: `${FAKE_URL}/v1`,
        E2E_FAKE_URL: FAKE_URL,
      },
    },
  ],
});
