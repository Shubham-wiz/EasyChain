import type { Proposal, TestCase } from "./copilot";
import type { TestRunDetail, TestRunResult, TestRunSummary, TestSetInfo, WorkspaceInfo, ApiKeyInfo, AuditPage, AuthStatus, NetworkSettings, SsoProvider, SsoSettings, Me, NewApiKey, Person, Role, Catalog, CheckResult, ChunkPreview, CompileResult, DataField, FlowListItem, FlowSpec, FlowVersion, InboxItem, Issue, KnowledgeBase, KnowledgeDocument, KnowledgeHit, McpServer, McpSettings, McpTool, NotificationSettings, OpenApiInspection, RunEvent, RunSummary, SandboxSettings, SandboxStatus, SavePoint, SecretInfo, Skill, SkillSummary, Step, TemplateInfo, ThreadInfo, Trigger, TriggerKind } from "./types";

export class ApiError extends Error {
  status: number;
  problems: string[];
  /** What to do: "sign_in" (401), "role" (403: an Owner can change your role), "workspace" (403: not in that workspace). */
  action?: string;
  constructor(status: number, message: string, problems: string[] = [], action?: string) {
    super(message);
    this.status = status;
    this.problems = problems;
    this.action = action;
  }
}

let signedOut: (() => void) | null = null;

/** Called when the server says nobody is signed in: the session ran out or was ended elsewhere. */
export function whenSignedOut(handler: (() => void) | null) {
  signedOut = handler;
}

// The workspace this tab works in (docs/workspaces.md): sent with every request. Kept for the
// tab (sessionStorage), and as the one new tabs start in (localStorage).
const WORKSPACE_KEY = "easychain.workspace";

function storedWorkspace(): string | null {
  try {
    return sessionStorage.getItem(WORKSPACE_KEY) ?? localStorage.getItem(WORKSPACE_KEY);
  } catch {
    return null; // storage switched off: the server picks the workspace
  }
}

let workspace: string | null = storedWorkspace();

/** The workspace this tab asked for (null: the server's choice, the default or your first). */
export function chosenWorkspace(): string | null {
  return workspace;
}

/** Work in another workspace from now on; the app then starts afresh in it. */
export function chooseWorkspace(id: string | null) {
  workspace = id;
  try {
    for (const store of [sessionStorage, localStorage]) {
      if (id) store.setItem(WORKSPACE_KEY, id);
      else store.removeItem(WORKSPACE_KEY);
    }
  } catch {
    // storage switched off: this page still sends it
  }
}

function workspaceHeader(): Record<string, string> {
  return workspace ? { "X-EasyChain-Workspace": workspace } : {};
}

let workspaceGone: (() => void) | null = null;

/** Called when the server says this tab's workspace isn't yours (any more). */
export function whenWorkspaceGone(handler: (() => void) | null) {
  workspaceGone = handler;
}

function failed(status: number, body: { message?: string; problems?: string[]; action?: string }, fallback: string): ApiError {
  if (status === 401 && body.action === "sign_in") signedOut?.();
  if (status === 403 && body.action === "workspace" && workspace) workspaceGone?.();
  return new ApiError(status, body.message ?? fallback, body.problems ?? [], body.action);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData bodies set their own multipart Content-Type.
  const isForm = typeof FormData !== "undefined" && init?.body instanceof FormData;
  const headers = { ...workspaceHeader(), ...((init?.headers as Record<string, string>) ?? {}) };
  const res = await fetch(path, {
    ...init,
    headers: isForm ? headers : { "Content-Type": "application/json", ...headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({})); // not JSON: the status says it
    throw failed(res.status, body, `${res.status} ${res.statusText}`);
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

const json = (body: unknown) => ({ body: JSON.stringify(body) });
const flowParam = (flowId?: string | null) => (flowId ? `?flow_id=${encodeURIComponent(flowId)}` : "");

export interface RunOptionsBody {
  flow_id?: string;
  spec?: FlowSpec;
  inputs: Record<string, unknown>;
  thread_id?: string;
  stand_in?: boolean;
  pause_before?: string[];
  pause_after?: string[];
}

/** POST or GET an endpoint that answers with Server-Sent Events; call onEvent for each. */
async function streamEvents(path: string, init: RequestInit, onEvent: (event: RunEvent) => void, signal?: AbortSignal): Promise<void> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...workspaceHeader(), ...((init.headers as Record<string, string>) ?? {}) },
    signal,
  });
  if (!res.ok || !res.body) {
    throw failed(res.status, await res.json().catch(() => ({})), "The run could not start");
  }
  await readSse(res.body, (data) => onEvent(JSON.parse(data) as RunEvent));
}

export const api = {
  // signing in, people and keys for scripts (server/auth_api.py)
  authStatus: () => request<AuthStatus>("/api/auth/status"),
  setup: (body: { code: string; email: string; name: string; password: string }) =>
    request<{ me: Me }>("/api/auth/setup", { method: "POST", ...json(body) }),
  signIn: (email: string, password: string) => request<{ me: Me }>("/api/auth/login", { method: "POST", ...json({ email, password }) }),
  signOut: () => request<{ signed_in: boolean }>("/api/auth/logout", { method: "POST" }),
  changePassword: (current: string, next: string) =>
    request<{ changed: boolean }>("/api/auth/password", { method: "POST", ...json({ current, new: next }) }),
  people: () => request<{ people: Person[] }>("/api/people").then((r) => r.people),
  /** Someone new needs a first password; someone with an account already keeps theirs (existing). */
  addPerson: (body: { email: string; name: string; role: Role; password?: string }) =>
    request<Person & { existing: boolean }>("/api/people", { method: "POST", ...json(body) }),
  updatePerson: (id: string, body: { role?: Role; disabled?: boolean; name?: string; password?: string }) =>
    request<Person>(`/api/people/${id}`, { method: "PATCH", ...json(body) }),
  removePerson: (id: string) => request<{ removed: string }>(`/api/people/${id}`, { method: "DELETE" }),
  workspaces: () => request<{ workspaces: WorkspaceInfo[]; current: string }>("/api/workspaces"),
  createWorkspace: (name: string) => request<WorkspaceInfo>("/api/workspaces", { method: "POST", ...json({ name }) }),
  renameWorkspace: (id: string, name: string) =>
    request<WorkspaceInfo>(`/api/workspaces/${encodeURIComponent(id)}`, { method: "PATCH", ...json({ name }) }),
  apiKeys: () => request<{ keys: ApiKeyInfo[] }>("/api/keys").then((r) => r.keys),
  createApiKey: (body: { name: string; role?: Role }) => request<NewApiKey>("/api/keys", { method: "POST", ...json(body) }),
  revokeApiKey: (id: string) => request<{ revoked: string }>(`/api/keys/${id}`, { method: "DELETE" }),
  sso: () => request<SsoSettings>("/api/settings/sso"),
  saveSso: (providers: SsoProvider[]) => request<SsoSettings>("/api/settings/sso", { method: "PUT", ...json({ providers }) }),
  network: () => request<NetworkSettings>("/api/settings/network"),
  saveNetwork: (body: { mode: NetworkSettings["mode"]; allow: string[] }) =>
    request<NetworkSettings>("/api/settings/network", { method: "PUT", ...json(body) }),
  audit: (filters: { kind?: string; person?: string; before?: number; limit?: number } = {}) => {
    const params = new URLSearchParams(Object.entries(filters).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]));
    return request<AuditPage>(`/api/audit?${params}`);
  },

  catalog: () => request<Catalog>("/api/catalog"),
  templates: () => request<TemplateInfo[]>("/api/templates"),
  templateSpec: (id: string) => request<FlowSpec>(`/api/templates/${encodeURIComponent(id)}`),
  flows: () => request<FlowListItem[]>("/api/flows"),
  flow: (id: string) => request<{ id: string; spec: FlowSpec }>(`/api/flows/${id}`),
  createFlow: (body: { template?: string; name?: string; spec?: FlowSpec; yaml?: string }) =>
    request<{ id: string; spec: FlowSpec }>("/api/flows", { method: "POST", ...json(body) }),
  saveFlow: (id: string, spec: FlowSpec, opts: { keepalive?: boolean } = {}) =>
    request<{ saved: boolean }>(`/api/flows/${id}`, { method: "PUT", ...json(spec), keepalive: opts.keepalive }),
  deleteFlow: (id: string) => request<{ deleted: boolean }>(`/api/flows/${id}`, { method: "DELETE" }),
  flowYaml: (id: string) => request<string>(`/api/flows/${id}/yaml`),
  check: (spec: FlowSpec, flowId?: string | null) =>
    request<CheckResult>(`/api/check${flowParam(flowId)}`, { method: "POST", ...json(spec) }),
  compile: (spec: FlowSpec, flowId?: string | null) =>
    request<CompileResult>(`/api/compile${flowParam(flowId)}`, { method: "POST", ...json(spec) }),
  runs: (flowId: string, opts: { thread?: string; status?: string } = {}) => {
    const params = new URLSearchParams({ flow_id: flowId });
    if (opts.thread) params.set("thread_id", opts.thread);
    if (opts.status) params.set("status", opts.status);
    return request<RunSummary[]>(`/api/runs?${params}`);
  },
  run: (runId: string) => request<RunSummary & { events: RunEvent[] }>(`/api/runs/${runId}`),
  cancelRun: (runId: string) => request<{ status: string }>(`/api/runs/${runId}/cancel`, { method: "POST" }),
  savePoints: (runId: string) => request<SavePoint[]>(`/api/runs/${runId}/savepoints`),
  threads: (flowId: string) => request<ThreadInfo[]>(`/api/threads${flowParam(flowId)}`),
  inbox: (status = "open") => request<InboxItem[]>(`/api/inbox?status=${status}`),
  answer: (itemId: string, body: { action: string; value?: unknown; comment?: string }) =>
    request<{ answered: boolean; run_id: string }>(`/api/inbox/${itemId}/answer`, { method: "POST", ...json(body) }),
  triggers: (flowId: string) => request<Trigger[]>(`/api/triggers${flowParam(flowId)}`),
  createTrigger: (body: { flow_id: string; kind: TriggerKind; name?: string; config: Record<string, unknown> }) =>
    request<Trigger>("/api/triggers", { method: "POST", ...json(body) }),
  updateTrigger: (id: string, body: { enabled?: boolean; name?: string; config?: Record<string, unknown> }) =>
    request<Trigger>(`/api/triggers/${id}`, { method: "PATCH", ...json(body) }),
  deleteTrigger: (id: string) => request<{ deleted: boolean }>(`/api/triggers/${id}`, { method: "DELETE" }),
  notifications: () => request<NotificationSettings>("/api/settings/notifications"),
  saveNotifications: (body: NotificationSettings) =>
    request<NotificationSettings>("/api/settings/notifications", { method: "PUT", ...json(body) }),
  testNotifications: () =>
    request<{ results: { channel: string; ok: boolean; error?: string }[] }>("/api/settings/notifications/test", { method: "POST" }),
  versions: (flowId: string) => request<FlowVersion[]>(`/api/flows/${flowId}/versions`),
  secrets: () => request<SecretInfo[]>("/api/secrets"),
  setSecret: (name: string, value: string) =>
    request<{ saved: boolean }>(`/api/secrets/${encodeURIComponent(name)}`, { method: "PUT", ...json({ value }) }),
  deleteSecret: (name: string) =>
    request<{ deleted: boolean }>(`/api/secrets/${encodeURIComponent(name)}`, { method: "DELETE" }),

  // Knowledge Bases
  knowledgeBases: () => request<KnowledgeBase[]>("/api/knowledge"),
  knowledgeBase: (id: string) =>
    request<KnowledgeBase & { documents: KnowledgeDocument[] }>(`/api/knowledge/${encodeURIComponent(id)}`),
  createKnowledgeBase: (body: { name: string; description?: string; embedding_model?: string; chunk_size?: number; chunk_overlap?: number }) =>
    request<KnowledgeBase>("/api/knowledge", { method: "POST", ...json(body) }),
  updateKnowledgeBase: (id: string, body: { name?: string; description?: string }) =>
    request<KnowledgeBase>(`/api/knowledge/${encodeURIComponent(id)}`, { method: "PATCH", ...json(body) }),
  deleteKnowledgeBase: (id: string) =>
    request<{ deleted: string }>(`/api/knowledge/${encodeURIComponent(id)}`, { method: "DELETE" }),
  addKnowledgeFiles: (id: string, files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    return request<KnowledgeDocument[]>(`/api/knowledge/${encodeURIComponent(id)}/files`, { method: "POST", body: form });
  },
  addKnowledgeUrls: (id: string, urls: string[]) =>
    request<KnowledgeDocument[]>(`/api/knowledge/${encodeURIComponent(id)}/urls`, { method: "POST", ...json({ urls }) }),
  addKnowledgeText: (id: string, title: string, text: string) =>
    request<KnowledgeDocument>(`/api/knowledge/${encodeURIComponent(id)}/text`, { method: "POST", ...json({ title, text }) }),
  deleteKnowledgeDocument: (id: string, docId: string) =>
    request<{ deleted: string }>(`/api/knowledge/${encodeURIComponent(id)}/documents/${docId}`, { method: "DELETE" }),
  knowledgeChunks: (id: string, docId: string) =>
    request<KnowledgeHit[]>(`/api/knowledge/${encodeURIComponent(id)}/documents/${docId}/chunks`),
  searchKnowledge: (id: string, query: string, opts: { top_k?: number; mode?: string } = {}) =>
    request<{ hits: KnowledgeHit[] }>(`/api/knowledge/${encodeURIComponent(id)}/search`, { method: "POST", ...json({ query, ...opts }) }),
  previewChunks: (input: { file?: File; url?: string; text?: string }, chunkSize: number, chunkOverlap: number) => {
    const form = new FormData();
    if (input.file) form.append("file", input.file);
    if (input.url) form.append("url", input.url);
    if (input.text) form.append("text", input.text);
    form.append("chunk_size", String(chunkSize));
    form.append("chunk_overlap", String(chunkOverlap));
    return request<ChunkPreview>("/api/knowledge-preview", { method: "POST", body: form });
  },

  // MCP servers and OpenAPI import
  copilot: (body: { request: string; spec: FlowSpec | null; issue?: Issue; model?: string }) =>
    request<Proposal>("/api/copilot", { method: "POST", ...json(body) }),
  copilotSettings: () => request<{ model: string | null; using: string | null }>("/api/settings/copilot"),
  saveCopilotSettings: (model: string | null) =>
    request<{ model: string | null; using: string | null }>("/api/settings/copilot", { method: "PUT", ...json({ model }) }),
  /** The flow's Test Set (`<flow id>.tests.yaml`): its cases, and the ${name} values they need. */
  testSet: (flowId: string) => request<TestSetInfo>(`/api/flows/${encodeURIComponent(flowId)}/tests`),
  saveTestSet: (flowId: string, body: { cases: TestCase[]; stand_in?: boolean }) =>
    request<{ saved: boolean; cases: number }>(`/api/flows/${encodeURIComponent(flowId)}/tests`, { method: "PUT", ...json(body) }),
  /** Run the saved cases (or `cases`, by name) now; `spec` runs the flow as it is on the canvas. */
  runTestSet: (flowId: string, body: { cases?: string[]; stand_in?: boolean; variables?: Record<string, string>; spec?: FlowSpec }) =>
    request<TestRunResult>(`/api/flows/${encodeURIComponent(flowId)}/tests/run`, { method: "POST", ...json(body) }),
  /** Kept Test Runs of a flow, newest first (5.2). */
  testRuns: (flowId: string) => request<{ runs: TestRunSummary[] }>(`/api/flows/${encodeURIComponent(flowId)}/test-runs`).then((r) => r.runs),
  startTestRun: (flowId: string, body: { repeat?: number; stand_in?: boolean; cases?: string[]; variables?: Record<string, string> }) =>
    request<{ id: string; status: string }>(`/api/flows/${encodeURIComponent(flowId)}/test-runs`, { method: "POST", ...json(body) }),
  testRun: (id: string) => request<TestRunDetail>(`/api/test-runs/${encodeURIComponent(id)}`),
  makeBaseline: (id: string) => request<{ baseline: string }>(`/api/test-runs/${encodeURIComponent(id)}/baseline`, { method: "POST" }),
  addTestCases: (flowId: string, cases: TestCase[]) =>
    request<{ added: number; total: number }>(`/api/flows/${encodeURIComponent(flowId)}/tests`, { method: "POST", ...json({ cases }) }),
  skills: () => request<{ skills: SkillSummary[] }>("/api/skills").then((r) => r.skills),
  skill: (name: string) => request<Skill>(`/api/skills/${encodeURIComponent(name)}`),
  saveSkill: (name: string, files: Record<string, string>) =>
    request<SkillSummary>(`/api/skills/${encodeURIComponent(name)}`, { method: "PUT", ...json({ files }) }),
  deleteSkill: (name: string) =>
    request<{ ok: boolean; built_in: SkillSummary | null }>(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" }),
  sandboxSettings: () => request<SandboxSettings>("/api/settings/sandbox"),
  saveSandboxSettings: (body: SandboxSettings) => request<SandboxSettings>("/api/settings/sandbox", { method: "PUT", ...json(body) }),
  sandboxStatus: (refresh = false) => request<SandboxStatus>(`/api/sandbox/status${refresh ? "?refresh=true" : ""}`),
  prepareSandbox: () => request<{ ok: boolean; message: string }>("/api/sandbox/prepare", { method: "POST" }),
  mcpSettings: () => request<McpSettings>("/api/settings/mcp"),
  saveMcpSettings: (body: McpSettings) => request<McpSettings>("/api/settings/mcp", { method: "PUT", ...json(body) }),
  mcpTools: (server: { server_id?: string; server?: McpServer }) =>
    request<{ tools: McpTool[] }>("/api/mcp/tools", { method: "POST", ...json(server) }),
  inspectOpenApi: (source: string) =>
    request<OpenApiInspection>("/api/openapi/inspect", { method: "POST", ...json({ source }) }),
  openApiSteps: (body: { source: string; operations: string[]; server?: string; auth_header?: string; auth_secret?: string; taken?: string[] }) =>
    request<{ steps: Step[]; data: DataField[] }>("/api/openapi/steps", { method: "POST", ...json(body) }),

  async exportZip(spec: FlowSpec, flowId?: string | null): Promise<Blob> {
    const res = await fetch(`/api/export${flowParam(flowId)}`, { method: "POST", headers: { "Content-Type": "application/json", ...workspaceHeader() }, ...json(spec) });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new ApiError(res.status, body.message ?? "Export failed");
    }
    return res.blob();
  },

  /** Start a run and call onEvent for every streamed event. Resolves when this part of the run ends. */
  runStream: (body: RunOptionsBody, onEvent: (event: RunEvent) => void, signal?: AbortSignal) =>
    streamEvents("/api/runs", { method: "POST", ...json(body) }, onEvent, signal),

  /** Answer waiting Ask a Human steps ({interrupt id: answer}) and follow the rest of the run. */
  resumeStream: (runId: string, answers: Record<string, unknown>, onEvent: (event: RunEvent) => void, signal?: AbortSignal) =>
    streamEvents(`/api/runs/${runId}/resume`, { method: "POST", ...json({ answers }) }, onEvent, signal),

  /** Carry on after a breakpoint or an error. */
  continueStream: (runId: string, onEvent: (event: RunEvent) => void, signal?: AbortSignal, step = false) =>
    streamEvents(`/api/runs/${runId}/continue${step ? "?step=true" : ""}`, { method: "POST" }, onEvent, signal),

  /** Re-run from a Save Point, optionally with changed Flow Data. */
  forkStream: (
    runId: string,
    body: { checkpoint_id: string; update?: Record<string, unknown> | null; pause_before?: string[]; pause_after?: string[] },
    onEvent: (event: RunEvent) => void,
    signal?: AbortSignal,
  ) => streamEvents(`/api/runs/${runId}/fork`, { method: "POST", ...json(body) }, onEvent, signal),

  /** Follow a run that is already going (after a reload, or one started elsewhere). */
  followStream: (runId: string, after: number, onEvent: (event: RunEvent) => void, signal?: AbortSignal) =>
    streamEvents(`/api/runs/${runId}/events?after=${after}`, { method: "GET" }, onEvent, signal),
};

/** Minimal Server-Sent Events reader for a fetch() body. */
export async function readSse(body: ReadableStream<Uint8Array>, onData: (data: string) => void): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.search(/\r?\n\r?\n/)) !== -1) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(buffer[sep] === "\r" ? sep + 4 : sep + 2);
      const data = block
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).replace(/^ /, ""))
        .join("\n");
      if (data) onData(data);
    }
  }
  const rest = buffer.trim();
  if (rest.startsWith("data:")) onData(rest.slice(5).trim());
}

export function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
