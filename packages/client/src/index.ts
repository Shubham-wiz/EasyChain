// A small client for the Easy Chain API: start runs, follow their events as they
// happen, answer Ask a Human steps, and stop, continue or re-run them.
//
//   const easy = new EasyChainClient({ baseUrl: "http://localhost:8000" });
//   for await (const event of easy.run({ flowId: "summarise-url", inputs: { url } })) {
//     if (event.type === "token") process.stdout.write(event.text);
//   }

import { readSse } from "./sse.js";
import type { Answer, RunEvent, RunInfo, StartOptions, Waiting } from "./types.js";

export type * from "./types.js";
export { readSse } from "./sse.js";

export class EasyChainError extends Error {
  status: number;
  kind?: string;
  constructor(status: number, message: string, kind?: string) {
    super(message);
    this.name = "EasyChainError";
    this.status = status;
    this.kind = kind;
  }
}

export interface ClientOptions {
  /** Where the Easy Chain server is, e.g. http://localhost:8000. Empty means this site. */
  baseUrl?: string;
  /** Extra headers for every request (for example a reverse proxy's auth header). */
  headers?: Record<string, string>;
  fetch?: typeof fetch;
}

function body(options: StartOptions, background: boolean) {
  return {
    flow_id: options.flowId,
    inputs: options.inputs ?? {},
    thread_id: options.threadId,
    stand_in: options.standIn ?? false,
    pause_before: options.pauseBefore ?? [],
    pause_after: options.pauseAfter ?? [],
    background,
  };
}

export class EasyChainClient {
  private readonly base: string;
  private readonly headers: Record<string, string>;
  private readonly fetcher: typeof fetch;

  constructor(options: ClientOptions = {}) {
    this.base = (options.baseUrl ?? "").replace(/\/$/, "");
    this.headers = options.headers ?? {};
    this.fetcher = options.fetch ?? ((...args) => globalThis.fetch(...args));
  }

  private async request(path: string, init: RequestInit = {}): Promise<Response> {
    const res = await this.fetcher(`${this.base}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...this.headers, ...((init.headers as Record<string, string>) ?? {}) },
    });
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))) as { message?: string; kind?: string };
      throw new EasyChainError(res.status, detail.message ?? `${res.status} ${res.statusText}`, detail.kind);
    }
    return res;
  }

  private async json<T>(path: string, init: RequestInit = {}): Promise<T> {
    return (await (await this.request(path, init)).json()) as T;
  }

  private async *events(path: string, init: RequestInit, signal?: AbortSignal): AsyncGenerator<RunEvent> {
    const res = await this.request(path, { ...init, signal, headers: { Accept: "text/event-stream" } });
    if (!res.body) return;
    for await (const data of readSse(res.body)) yield JSON.parse(data) as RunEvent;
  }

  /** Start a run and follow it until it finishes, fails, is stopped or waits for someone. */
  run(options: StartOptions, signal?: AbortSignal): AsyncGenerator<RunEvent> {
    return this.events("/api/runs", { method: "POST", body: JSON.stringify(body(options, false)) }, signal);
  }

  /** Start a run in the background; follow it later with follow() or wait(). */
  start(options: StartOptions): Promise<{ run_id: string; thread_id: string }> {
    return this.json("/api/runs", { method: "POST", body: JSON.stringify(body(options, true)) });
  }

  /** Events of a run from `after` (an event_id) on, as they arrive. Safe to call again after a disconnect. */
  follow(runId: string, after = 0, signal?: AbortSignal): AsyncGenerator<RunEvent> {
    return this.events(`/api/runs/${encodeURIComponent(runId)}/events?after=${after}`, { method: "GET" }, signal);
  }

  /** Follow a run to the end of its current part and return the final event. */
  async wait(runId: string, signal?: AbortSignal): Promise<RunEvent> {
    let last: RunEvent | undefined;
    let after = 0;
    for (;;) {
      for await (const event of this.follow(runId, after, signal)) {
        last = event;
        after = event.event_id ?? after;
      }
      if (last?.type === "run_finished") return last;
      const run = await this.getRun(runId);
      if (!["queued", "running"].includes(run.status)) return last ?? ({ type: "run_finished", run_id: runId, ts: Date.now(), status: run.status } as RunEvent);
    }
  }

  getRun(runId: string): Promise<RunInfo & { events: RunEvent[] }> {
    return this.json(`/api/runs/${encodeURIComponent(runId)}`);
  }

  listRuns(filter: { flowId?: string; threadId?: string; status?: string } = {}): Promise<RunInfo[]> {
    const params = new URLSearchParams();
    if (filter.flowId) params.set("flow_id", filter.flowId);
    if (filter.threadId) params.set("thread_id", filter.threadId);
    if (filter.status) params.set("status", filter.status);
    return this.json(`/api/runs?${params}`);
  }

  /** Stop a run. It keeps its Save Points, so it can be continued. */
  cancel(runId: string): Promise<{ status: string }> {
    return this.json(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
  }

  /** Answer waiting Ask a Human steps ({waiting id: answer}) and follow the rest of the run. */
  resume(runId: string, answers: Record<string, Answer | unknown>, signal?: AbortSignal): AsyncGenerator<RunEvent> {
    return this.events(`/api/runs/${encodeURIComponent(runId)}/resume`, { method: "POST", body: JSON.stringify({ answers }) }, signal);
  }

  /** Carry on after a breakpoint, an error or a stop. */
  continue(runId: string, signal?: AbortSignal): AsyncGenerator<RunEvent> {
    return this.events(`/api/runs/${encodeURIComponent(runId)}/continue`, { method: "POST" }, signal);
  }

  /** Re-run from a Save Point, optionally changing Flow Data there first. */
  fork(runId: string, checkpointId: string, update?: Record<string, unknown>, signal?: AbortSignal): AsyncGenerator<RunEvent> {
    return this.events(
      `/api/runs/${encodeURIComponent(runId)}/fork`,
      { method: "POST", body: JSON.stringify({ checkpoint_id: checkpointId, update: update ?? null }) },
      signal,
    );
  }

  inbox(): Promise<{ id: string; run_id: string; flow_name: string; request: Waiting["request"] }[]> {
    return this.json("/api/inbox");
  }

  answer(inboxId: string, answer: Answer): Promise<{ answered: boolean; run_id: string }> {
    return this.json(`/api/inbox/${encodeURIComponent(inboxId)}/answer`, { method: "POST", body: JSON.stringify(answer) });
  }

  /** A WebSocket that sends every event of a run as JSON; send {"type": "cancel"} to stop it. */
  socket(runId: string, after = 0): WebSocket {
    const base = this.base || (typeof location !== "undefined" ? location.origin : "");
    const url = `${base.replace(/^http/, "ws")}/api/runs/${encodeURIComponent(runId)}/ws?after=${after}`;
    return new WebSocket(url);
  }
}
