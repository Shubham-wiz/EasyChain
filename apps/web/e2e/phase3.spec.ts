// Phase 3 journeys: an Agent with step tools (live tool calls, approvals), the structured
// reply builder, Knowledge Bases and cited answers, MCP servers and importing an API.

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { FAKE_URL, inspect, MCP_URL, step } from "./helpers";

type Spec = Record<string, unknown>;

const input = (...fields: string[]) => ({ id: "input", type: "input", name: "Input", settings: { fields: fields.map((name) => ({ name })) } });
const output = (...fields: string[]) => ({ id: "output", type: "output", name: "Output", settings: { fields } });

const readPage = {
  id: "read_page",
  type: "http_request",
  name: "Read a page",
  description: "Reads a page about a topic.",
  settings: { url: `${FAKE_URL}/pages/{page_name}`, save_as: "page" },
};
const sendNote = {
  id: "send_note",
  type: "http_request",
  name: "Send a note",
  description: "Sends a note to the team.",
  settings: { method: "POST", url: `${FAKE_URL}/effects`, body: '{"note": "{note}"}', response: "json", save_as: "receipt" },
};

function agentFlow(name: string, agent: Record<string, unknown>, tools: Spec[]): Spec {
  return {
    name,
    data: [
      { name: "page_name", description: "Short name of the page, like bees." },
      { name: "note", description: "The note to send." },
    ],
    steps: [input("question"), { id: "helper", type: "agent", name: "Helper", settings: { input: "question", ...agent } }, ...tools, output("answer")],
    connections: [
      { from: "input", to: "helper" },
      { from: "helper", to: "output" },
    ],
    canvas: {
      steps: {
        input: { x: 0, y: 100 },
        helper: { x: 320, y: 100 },
        output: { x: 640, y: 100 },
        ...Object.fromEntries(tools.map((t, i) => [t.id as string, { x: 200 + i * 300, y: 340 }])),
      },
      notes: [],
    },
  };
}

async function createFlow(request: APIRequestContext, spec: Spec): Promise<string> {
  const res = await request.post("/api/flows", { data: { spec } });
  expect(res.ok()).toBeTruthy();
  return (await res.json()).id;
}

async function openFlow(page: Page, id: string) {
  await page.goto(`/#/flows/${id}`);
  await expect(step(page, "input")).toBeVisible();
}

async function axe(page: Page, include?: string) {
  let builder = new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).exclude(".react-flow__minimap");
  if (include) builder = builder.include(include);
  const serious = (await builder.analyze()).violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  for (const v of serious) console.log(`${v.id}: ${v.help}\n  ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join("\n  ")}`);
  return serious;
}

test("Agent: tools hang under the agent, and its tool calls show as they happen", async ({ page, request }) => {
  const id = await createFlow(request, agentFlow("Agent with a tool", { tools: ["read_page"] }, [readPage]));
  await openFlow(page, id);
  await expect(page.getByTestId("tool-of-read_page")).toContainText("Tool of Helper");
  await expect(page.locator('.react-flow__edge[data-id^="tool:read_page"]')).toHaveCount(1);
  await expect(page.getByTestId("problems-button")).toContainText("No problems");

  await inspect(page, "helper");
  await expect(page.getByTestId("agent-tool-read_page")).toContainText("Reads a page about a topic.");
  expect(await axe(page, "aside")).toEqual([]);

  await page.getByTestId("open-run").click();
  await page.getByLabel("question").fill("Tell me about bees. page_name: bees");
  await page.getByTestId("run-submit").click();
  await expect(page.getByTestId("run-output")).toContainText("colonies");
  await expect(page.getByTestId("tool-calls-helper")).toContainText("read_page");
  await expect(step(page, "helper")).toHaveAttribute("data-status", "done");
});

test("Agent: a person approves a tool call before it runs", async ({ page, request }) => {
  await request.post(`${FAKE_URL}/effects/reset`);
  await request.post(`${FAKE_URL}/__script`, { data: [{ call: "send_note", args: { note: "hi from the agent" } }, "I sent the note."] });
  const id = await createFlow(request, agentFlow("Agent that asks first", { tools: ["send_note"], addons: { approve_tools: ["send_note"] } }, [sendNote]));
  await openFlow(page, id);
  await page.getByTestId("open-run").click();
  await page.getByLabel("question").fill("Please send a note to the team");
  await page.getByTestId("run-submit").click();

  const waiting = page.getByTestId("run-waiting");
  await expect(waiting).toContainText("send_note");
  await expect(waiting).toContainText("hi from the agent");
  expect((await (await request.get(`${FAKE_URL}/effects`)).json()).applied).toEqual([]);
  await waiting.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("run-output")).toContainText("I sent the note.");
  const applied = (await (await request.get(`${FAKE_URL}/effects`)).json()).applied;
  expect(applied.map((a: { body: unknown }) => a.body)).toEqual([{ note: "hi from the agent" }]);
});

test("Agent inspector: add a new tool and switch on an add-on", async ({ page, request }) => {
  const id = await createFlow(request, agentFlow("Agent without tools", { tools: [] }, []));
  await openFlow(page, id);
  await inspect(page, "helper");
  await expect(page.getByTestId("agent-tools")).toContainText("No tools yet");
  await page.getByLabel("Add a new tool").selectOption({ label: "Web request" });
  const added = page.locator('[data-testid^="tool-of-"]');
  await expect(added).toHaveCount(1);
  await expect(added).toContainText("Tool of Helper");

  await inspect(page, "helper");
  const tool = page.locator('[data-testid^="agent-tool-"]');
  await expect(tool).toHaveCount(1);
  await expect(tool).toContainText("Describe this tool");
  await tool.getByRole("switch").click();
  await expect(tool.getByRole("switch")).toBeChecked();
  await expect(page.getByTestId("agent-addons")).toBeVisible();
});

test("Structured reply: fixed fields become Flow Data that later steps use", async ({ page, request }) => {
  const id = await createFlow(request, {
    name: "Rate feedback",
    steps: [input("feedback"), { id: "rate", type: "ai_model", name: "Rate it", settings: { prompt: "feedback", save_as: "rating" } }, output("mood")],
    connections: [
      { from: "input", to: "rate" },
      { from: "rate", to: "output" },
    ],
    canvas: { steps: { input: { x: 0, y: 100 }, rate: { x: 320, y: 100 }, output: { x: 640, y: 100 } }, notes: [] },
  });
  await openFlow(page, id);
  await expect(page.getByTestId("problems-button")).not.toContainText("No problems");
  await inspect(page, "rate");
  const builder = page.getByTestId("schema-builder");
  await builder.getByRole("radio", { name: "Fixed fields" }).click();
  const field = builder.getByTestId("schema-field-answer");
  await field.getByLabel("Field name").fill("mood");
  await field.getByLabel("Field name").blur();
  const mood = builder.getByTestId("schema-field-mood");
  await mood.getByLabel("Kind of value").selectOption("choice");
  await mood.getByLabel("Choices").fill("happy, unhappy");
  await mood.getByLabel("Choices").blur();
  await expect(page.getByTestId("problems-button")).toContainText("No problems");
  expect(await axe(page, "aside")).toEqual([]);

  await page.getByTestId("open-run").click();
  await page.getByLabel("feedback").fill("The tea arrived quickly and tastes great");
  await page.getByTestId("run-submit").click();
  await expect(page.getByTestId("run-output")).toContainText(/happy|unhappy/);
});

test("Knowledge Bases: create one, add text, and search it with sources", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("knowledge-link").click();
  const bases = page.getByTestId("knowledge-bases");
  await expect(bases).toContainText("Help centre");
  expect(await axe(page)).toEqual([]);

  await page.getByTestId("new-kb-button").click();
  await page.getByLabel("Name").fill("Office notes");
  await page.getByLabel("What's in it").fill("Notes for people visiting the office");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page).toHaveURL(/#\/knowledge\/office_notes/);

  await page.getByRole("tab", { name: "Text" }).click();
  await page.getByLabel("Title").fill("Parking");
  await page.getByLabel("Text").fill("Staff park behind the warehouse. Visitors park on Elm Street, next to the bakery.");
  await page.getByRole("button", { name: "Add text" }).click();
  await expect(page.getByTestId("kb-documents")).toContainText("Parking");
  await expect(page.getByTestId("kb-document").filter({ hasText: "Parking" })).toContainText("passages", { timeout: 20_000 });

  const search = page.getByRole("region", { name: "Try a search" });
  await search.getByLabel("Question").fill("Where do visitors park?");
  await search.getByRole("button", { name: "Search" }).click();
  await expect(search).toContainText("Elm Street");
  await expect(search).toContainText("Parking");
  expect(await axe(page)).toEqual([]);
});

test("Support bot: answers from the help centre and links its citations", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-support-bot").getByRole("button", { name: "Use template" }).click();
  await expect(step(page, "search")).toBeVisible();
  await page.getByTestId("open-run").click();
  await page.getByTestId("chat-input").fill("How long do refunds take?");
  await page.keyboard.press("Enter");
  const log = page.getByTestId("chat-log");
  await expect(log.getByRole("link", { name: "Source 1" }).first()).toBeVisible({ timeout: 30_000 });
  await expect(log).toContainText(/refund/i);
  await expect(log).toContainText("returns");
  await expect(log).not.toContainText("## ");
});

test("MCP: connect a server in Settings, then call its tool from a step", async ({ page, request }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("tab", { name: "MCP servers" }).click();
  const section = page.getByTestId("mcp-settings");
  await section.getByRole("button", { name: "Add a server" }).click();
  const card = section.getByTestId("mcp-server-server_1");
  await card.getByLabel("Server name").fill("Facts");
  await card.getByLabel("Server id").fill("facts");
  await card.getByLabel("Server id").blur();
  await page.getByTestId("mcp-server-facts").getByLabel("Server URL").fill(MCP_URL);
  await page.getByTestId("mcp-server-facts").getByRole("button", { name: "Show its tools" }).click();
  await expect(page.getByTestId("mcp-tools-list")).toContainText("city_facts");
  expect(await axe(page, '[role="dialog"]')).toEqual([]);
  await section.getByRole("button", { name: "Save MCP servers" }).click();
  await expect(section).toContainText("Saved.");
  await page.keyboard.press("Escape");

  const id = await createFlow(request, {
    name: "City facts",
    steps: [input("city"), { id: "facts", type: "mcp_tool", name: "Look up the city", settings: { save_as: "about" } }, output("about")],
    connections: [
      { from: "input", to: "facts" },
      { from: "facts", to: "output" },
    ],
    canvas: { steps: { input: { x: 0, y: 100 }, facts: { x: 320, y: 100 }, output: { x: 640, y: 100 } }, notes: [] },
  });
  await openFlow(page, id);
  await inspect(page, "facts");
  await page.getByLabel("MCP server", { exact: true }).selectOption("facts");
  await page.getByLabel("Tool", { exact: true }).selectOption("city_facts");
  await expect(page.getByText("Facts about a city")).toBeVisible();
  // A normal connection leaves from the right-hand handle, not the "use as a tool" one on top.
  const edgeBox = (await page.locator('.react-flow__edge[data-id="facts->output"] path').first().boundingBox())!;
  const nodeBox = (await step(page, "facts").boundingBox())!;
  expect(edgeBox.y).toBeGreaterThan(nodeBox.y);
  expect(edgeBox.x).toBeGreaterThan(nodeBox.x + nodeBox.width - 10);
  const city = page.getByLabel("Value (city)");
  await city.fill("{city}");
  await city.blur();
  await expect(page.getByTestId("problems-button")).toContainText("No problems");

  await page.getByTestId("open-run").click();
  await page.getByLabel("city", { exact: true }).fill("Lisbon");
  await page.getByTestId("run-submit").click();
  await expect(page.getByTestId("run-output")).toContainText("Tagus");
});

const PETS_API = JSON.stringify({
  openapi: "3.0.0",
  info: { title: "Pets", version: "1" },
  servers: [{ url: `${FAKE_URL}/pets-api` }],
  paths: {
    "/pets/{petId}": {
      get: {
        operationId: "getPet",
        summary: "Find a pet by its id",
        parameters: [{ name: "petId", in: "path", required: true, schema: { type: "integer" }, description: "The pet's id" }],
      },
    },
    "/pets": {
      get: { operationId: "listPets", summary: "List all pets" },
      post: { operationId: "addPet", summary: "Add a pet" },
    },
  },
});

test("Import an API: picked operations become an agent's tools", async ({ page, request }) => {
  const id = await createFlow(request, agentFlow("Pet helper", { tools: [] }, []));
  await openFlow(page, id);
  await page.getByRole("button", { name: "Import an API" }).click();
  const dialog = page.getByTestId("import-api");
  await dialog.getByLabel("OpenAPI address or text").fill(PETS_API);
  await dialog.getByRole("button", { name: "Read it" }).click();
  await expect(dialog).toContainText("Pets has 3 operations");
  await dialog.getByRole("checkbox").nth(0).check();
  await dialog.getByRole("checkbox").nth(1).check();
  await page.getByLabel("Add them").selectOption("helper");
  expect(await axe(page, '[role="dialog"]')).toEqual([]);
  await page.getByTestId("import-api-add").click();
  await expect(dialog).toBeHidden();
  await expect(page.locator('[data-testid^="tool-of-"]')).toHaveCount(2);
  await inspect(page, "helper");
  await expect(page.locator('[data-testid^="agent-tool-"]')).toHaveCount(2);
  await expect(page.getByTestId("agent-tools")).toContainText(/Find a pet by its id|List all pets/);
});
