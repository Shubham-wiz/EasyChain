import type {
  Catalog,
  CheckResult,
  CompileResult,
  FlowListItem,
  FlowSpec,
  RunEvent,
  RunSummary,
  SecretInfo,
  TemplateInfo,
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
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
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
  check: (spec: FlowSpec) => request<CheckResult>("/api/check", { method: "POST", ...json(spec) }),
  compile: (spec: FlowSpec) => request<CompileResult>("/api/compile", { method: "POST", ...json(spec) }),
  runs: (flowId: string) => request<RunSummary[]>(`/api/runs?flow_id=${encodeURIComponent(flowId)}`),
  run: (runId: string) => request<RunSummary & { events: RunEvent[] }>(`/api/runs/${runId}`),
  secrets: () => request<SecretInfo[]>("/api/secrets"),
  setSecret: (name: string, value: string) =>
    request<{ saved: boolean }>(`/api/secrets/${encodeURIComponent(name)}`, { method: "PUT", ...json({ value }) }),
  deleteSecret: (name: string) =>
    request<{ deleted: boolean }>(`/api/secrets/${encodeURIComponent(name)}`, { method: "DELETE" }),

  async exportZip(spec: FlowSpec): Promise<Blob> {
    const res = await fetch("/api/export", { method: "POST", headers: { "Content-Type": "application/json" }, ...json(spec) });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new ApiError(res.status, body.message ?? "Export failed");
    }
    return res.blob();
  },

  /** Start a run and call onEvent for every streamed event. Resolves when the stream ends. */
  async runStream(
    body: { flow_id?: string; spec?: FlowSpec; inputs: Record<string, unknown>; thread_id?: string; stand_in?: boolean },
    onEvent: (event: RunEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const res = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok || !res.body) {
      const err = await res.json().catch(() => ({}));
      throw new ApiError(res.status, err.message ?? "The run could not start", err.problems ?? []);
    }
    await readSse(res.body, (data) => onEvent(JSON.parse(data) as RunEvent));
  },
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
