import type {
  Catalog,
  CheckResult,
  ChunkPreview,
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeHit,
  McpServer,
  McpSettings,
  McpTool,
  OpenApiInspection,
  Step,
  DataField,
  CompileResult,
  FlowListItem,
  FlowSpec,
  FlowVersion,
  InboxItem,
  NotificationSettings,
  RunEvent,
  RunSummary,
  SavePoint,
  SecretInfo,
  TemplateInfo,
  ThreadInfo,
  Trigger,
  TriggerKind,
} from "./types";

export class ApiError extends Error {
  status: number;
  problems: string[];
  constructor(status: number, message: string, problems: string[] = []) {
    super(message);
    this.status = status;
    this.problems = problems;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData bodies set their own multipart Content-Type.
  const isForm = typeof FormData !== "undefined" && init?.body instanceof FormData;
  const res = await fetch(path, {
    ...init,
    headers: isForm ? (init?.headers ?? {}) : { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    let problems: string[] = [];
    try {
      const body = await res.json();
      message = body.message ?? message;
      problems = body.problems ?? [];
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message, problems);
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
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...(init.headers ?? {}) },
    signal,
  });
  if (!res.ok || !res.body) {
    const err = await res.json().catch(() => ({}));
    throw new ApiError(res.status, err.message ?? "The run could not start", err.problems ?? []);
  }
  await readSse(res.body, (data) => onEvent(JSON.parse(data) as RunEvent));
}

export const api = {
  catalog: () => request<Catalog>("/api/catalog"),
  templates: () => request<TemplateInfo[]>("/api/templates"),
  flows: () => request<FlowListItem[]>("/api/flows"),
  flow: (id: string) => request<{ id: string; spec: FlowSpec }>(`/api/flows/${id}`),
  createFlow: (body: { template?: string; name?: string; spec?: FlowSpec; yaml?: string }) =>
    request<{ id: string; spec: FlowSpec }>("/api/flows", { method: "POST", ...json(body) }),
  saveFlow: (id: string, spec: FlowSpec) =>
    request<{ saved: boolean }>(`/api/flows/${id}`, { method: "PUT", ...json(spec) }),
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
  mcpSettings: () => request<McpSettings>("/api/settings/mcp"),
  saveMcpSettings: (body: McpSettings) => request<McpSettings>("/api/settings/mcp", { method: "PUT", ...json(body) }),
  mcpTools: (server: { server_id?: string; server?: McpServer }) =>
    request<{ tools: McpTool[] }>("/api/mcp/tools", { method: "POST", ...json(server) }),
  inspectOpenApi: (source: string) =>
    request<OpenApiInspection>("/api/openapi/inspect", { method: "POST", ...json({ source }) }),
  openApiSteps: (body: { source: string; operations: string[]; server?: string; auth_header?: string; auth_secret?: string; taken?: string[] }) =>
    request<{ steps: Step[]; data: DataField[] }>("/api/openapi/steps", { method: "POST", ...json(body) }),

  async exportZip(spec: FlowSpec, flowId?: string | null): Promise<Blob> {
    const res = await fetch(`/api/export${flowParam(flowId)}`, { method: "POST", headers: { "Content-Type": "application/json" }, ...json(spec) });
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
