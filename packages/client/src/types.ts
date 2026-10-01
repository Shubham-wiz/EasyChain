// The shapes the Easy Chain API sends. Events stream as JSON, one per run event.

export type RunStatus = "queued" | "running" | "paused" | "ok" | "error" | "cancelled";

export interface AskRequest {
  step?: string;
  kind: "approve" | "edit" | "answer" | "choose";
  question: string;
  show?: Record<string, unknown>;
  field?: string;
  value?: unknown;
  options?: string[];
}

/** An Ask a Human step waiting for an answer. */
export interface Waiting {
  id: string;
  step: string | null;
  path: string[];
  request: AskRequest;
}

export interface RunError {
  kind: string;
  message: string;
  hint?: string;
  detail?: string;
}

/** One run event. `type` says which; the other keys depend on it (see the API docs). */
export interface RunEvent {
  type:
    | "run_queued"
    | "run_started"
    | "step_started"
    | "token"
    | "step_finished"
    | "route"
    | "progress"
    | "step_paused"
    | "save_point"
    | "step_failed"
    | "paused"
    | "notified"
    | "run_finished"
    | (string & {});
  run_id: string;
  ts: number;
  event_id?: number;
  step?: string;
  path?: string[];
  text?: string;
  output?: Record<string, unknown>;
  status?: Exclude<RunStatus, "queued" | "running">;
  error?: RunError;
  reason?: "ask_human" | "breakpoint";
  interrupts?: Waiting[];
  reply?: string | null;
  thread_id?: string;
  [key: string]: unknown;
}

export interface RunInfo {
  run_id: string;
  flow_id: string | null;
  flow: string;
  thread_id?: string;
  status: RunStatus;
  inputs: Record<string, unknown>;
  output?: Record<string, unknown> | null;
  error?: RunError | null;
  pending?: { reason: string; interrupts: Waiting[]; next: string[] } | null;
}

export interface Answer {
  action?: "approve" | "reject";
  value?: unknown;
  comment?: string;
}

export interface StartOptions {
  /** A saved flow's id (its file name without .flow.yaml). */
  flowId: string;
  inputs?: Record<string, unknown>;
  /** Keep a conversation going (chat flows), or a fresh one when left out. */
  threadId?: string;
  standIn?: boolean;
  pauseBefore?: string[];
  pauseAfter?: string[];
}
