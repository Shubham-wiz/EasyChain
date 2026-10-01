import { create } from "zustand";
import { api, ApiError } from "../lib/api";
import type { AskRequest, Issue, RunError, RunEvent, RunStatus, Usage, Waiting } from "../lib/types";
import { useCheck } from "./check";
import { useFlow } from "./flow";
import { useUi } from "./ui";

export type StepStatus = "running" | "done" | "error" | "waiting";

/** Something that happened inside a Sub-flow step, shown under it in the trace. */
export interface InnerEvent {
  path: string[];
  step: string;
  type: "started" | "finished" | "failed" | "paused" | "route";
  output?: Record<string, unknown>;
  error?: RunError;
  exit?: string;
  durationMs?: number;
}

export interface StepRun {
  status: StepStatus;
  tokens: string;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  durationMs?: number;
  usage?: Usage;
  cost?: number | null;
  model?: string | null;
  error?: RunError;
  exit?: string;
  /** For Each: how many items are done of how many. */
  progress?: { done: number; total: number };
  /** The step run once per item: what each item produced. */
  items?: Record<number, { status: StepStatus; output?: Record<string, unknown> }>;
  /** Ask a Human: what it is asking. */
  request?: AskRequest;
  inner?: InnerEvent[];
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
  failed?: boolean;
}

export interface SavePointMark {
  checkpoint_id: string;
  next: string[];
  step_number: number | null;
}

type Finished = Extract<RunEvent, { type: "run_finished" }>;

interface RunState {
  runId: string | null;
  status: "idle" | RunStatus;
  steps: Record<string, StepRun>;
  order: string[];
  events: RunEvent[];
  final: Finished | null;
  error: RunError | null;
  issues: Issue[];
  standIn: boolean;
  replaying: boolean;
  threadId: string | null;
  chat: ChatMessage[];
  /** Ask a Human steps waiting for an answer, or the step a breakpoint stopped before. */
  waiting: Waiting[];
  pauseReason: "ask_human" | "breakpoint" | null;
  next: string[];
  savePoints: SavePointMark[];
  lastEventId: number;
  /** The inputs of the last form run, so the form and Retry can reuse them. */
  lastInputs: Record<string, unknown>;
  controller: AbortController | null;
  reset: () => void;
  handle: (event: RunEvent) => void;
  newChat: () => void;
}

const empty = {
  runId: null,
  status: "idle" as const,
  steps: {},
  order: [],
  events: [],
  final: null,
  error: null,
  issues: [],
  standIn: false,
  replaying: false,
  waiting: [],
  pauseReason: null,
  next: [],
  savePoints: [],
  lastEventId: 0,
};

/** Steps whose AI reply is the chat answer (they save into `messages`). */
function chatReplySteps(): Set<string> {
  const writes = useCheck.getState().analysis?.writes ?? {};
  return new Set(Object.entries(writes).filter(([, w]) => "messages" in w).map(([id]) => id));
}

function withStep(state: RunState, id: string, patch: (prev: StepRun) => StepRun): Pick<RunState, "steps" | "order"> {
  const prev = state.steps[id] ?? { status: "running" as const, tokens: "" };
  return {
    steps: { ...state.steps, [id]: patch(prev) },
    order: state.order.includes(id) ? state.order : [...state.order, id],
  };
}

function handleInner(state: RunState, event: RunEvent & { path: string[] }): Partial<RunState> {
  const outer = event.path[0];
  const note = (inner: InnerEvent) => withStep(state, outer, (prev) => ({ ...prev, inner: [...(prev.inner ?? []), inner] }));
  switch (event.type) {
    case "token":
      return withStep(state, outer, (prev) => ({ ...prev, tokens: prev.tokens + event.text }));
    case "step_started":
      return note({ path: event.path, step: event.step, type: "started" });
    case "step_finished":
      return note({ path: event.path, step: event.step, type: "finished", output: event.output, durationMs: event.duration_ms });
    case "step_failed":
      return note({ path: event.path, step: event.step, type: "failed", error: event.error });
    case "route":
      return note({ path: event.path, step: event.step, type: "route", exit: event.exit });
    case "step_paused":
      return withStep(state, outer, (prev) => ({
        ...prev,
        status: "waiting",
        request: event.request,
        inner: [...(prev.inner ?? []), { path: event.path, step: event.step, type: "paused" }],
      }));
    default:
      return {};
  }
}

export const useRun = create<RunState>()((set, get) => ({
  ...empty,
  threadId: null,
  chat: [],
  lastInputs: {},
  controller: null,
  reset: () => set({ ...empty }),
  newChat: () => set({ ...empty, threadId: null, chat: [], lastInputs: {} }),
  handle: (event) => {
    const state = get();
    const events = event.type === "token" ? state.events : [...state.events, event];
    const lastEventId = Math.max(state.lastEventId, event.event_id ?? 0);
    if (event.path?.length && event.type !== "run_finished") {
      set({ ...handleInner(state, event as RunEvent & { path: string[] }), events, lastEventId });
      return;
    }
    switch (event.type) {
      case "run_queued":
        set({
          runId: event.run_id,
          status: state.status === "idle" || state.status === "paused" || state.status === "error" ? "queued" : state.status,
          events,
          lastEventId,
          waiting: [],
          pauseReason: null,
        });
        return;
      case "run_started":
        set({
          runId: event.run_id,
          status: "running",
          standIn: event.stand_in,
          events,
          lastEventId,
          threadId: event.thread_id,
          waiting: [],
          pauseReason: null,
          error: null,
        });
        return;
      case "step_started": {
        if (event.item != null) {
          set({
            ...withStep(state, event.step, (prev) => ({
              ...prev,
              status: "running",
              input: event.input,
              items: { ...(prev.items ?? {}), [event.item!]: { status: "running" } },
            })),
            events,
            lastEventId,
          });
          return;
        }
        set({
          ...withStep(state, event.step, (prev) => ({
            ...prev,
            status: "running",
            tokens: "",
            input: event.input,
            error: undefined,
            request: undefined,
            exit: prev.status === "waiting" ? prev.exit : undefined,
          })),
          events,
          lastEventId,
        });
        return;
      }
      case "token": {
        const prev = state.steps[event.step] ?? { status: "running" as const, tokens: "" };
        const steps = { ...state.steps, [event.step]: { ...prev, tokens: prev.tokens + event.text } };
        let chat = state.chat;
        if (chatReplySteps().has(event.step) && chat.length && chat[chat.length - 1].pending) {
          const last = chat[chat.length - 1];
          chat = [...chat.slice(0, -1), { ...last, content: last.content + event.text }];
        }
        set({ steps, chat, lastEventId });
        return;
      }
      case "step_finished": {
        set({
          ...withStep(state, event.step, (prev) => {
            const base = {
              ...prev,
              output: event.output,
              durationMs: event.duration_ms,
              usage: event.usage ?? prev.usage,
              cost: event.cost ?? prev.cost,
              model: event.model ?? prev.model,
            };
            if (event.item == null) return { ...base, status: "done" as const, request: undefined };
            const items = { ...(prev.items ?? {}), [event.item]: { status: "done" as const, output: event.output } };
            const busy = Object.values(items).some((i) => i.status === "running");
            return { ...base, items, status: busy ? ("running" as const) : ("done" as const) };
          }),
          events,
          lastEventId,
        });
        return;
      }
      case "route":
        set({ ...withStep(state, event.step, (prev) => ({ ...prev, exit: event.exit })), events, lastEventId });
        return;
      case "progress":
        set({ ...withStep(state, event.step, (prev) => ({ ...prev, progress: { done: event.done, total: event.total } })), events, lastEventId });
        return;
      case "step_paused":
        set({ ...withStep(state, event.step, (prev) => ({ ...prev, status: "waiting", request: event.request })), events, lastEventId });
        return;
      case "save_point":
        set({
          savePoints: [...state.savePoints, { checkpoint_id: event.checkpoint_id, next: event.next, step_number: event.step_number }],
          events,
          lastEventId,
        });
        return;
      case "step_failed":
        set({ ...withStep(state, event.step, (prev) => ({ ...prev, status: "error", error: event.error })), events, lastEventId });
        return;
      case "paused":
        set({ waiting: event.interrupts, pauseReason: event.reason, next: event.next, events, lastEventId });
        return;
      case "run_finished": {
        let chat = state.chat;
        if (chat.length && chat[chat.length - 1].pending) {
          const last = chat[chat.length - 1];
          if (event.status === "ok") chat = [...chat.slice(0, -1), { role: "assistant", content: event.reply ?? last.content }];
          else if (event.status === "paused") chat = [...chat.slice(0, -1), { role: "assistant", content: last.content || "Waiting for an answer…", pending: true }];
          else chat = [...chat.slice(0, -1), { role: "assistant", content: event.error?.message ?? "The run was stopped.", failed: true }];
        }
        // Steps still marked running didn't finish (a cancel or a breakpoint).
        const steps = { ...state.steps };
        if (event.status !== "paused") {
          for (const [id, run] of Object.entries(steps)) if (run.status === "running") steps[id] = { ...run, status: event.status === "error" ? "error" : "done" };
        }
        set({
          status: event.status,
          final: event,
          error: event.error ?? null,
          issues: event.issues ?? [],
          events,
          chat,
          steps,
          controller: null,
          lastEventId,
          waiting: event.status === "paused" ? event.interrupts ?? state.waiting : [],
          pauseReason: event.status === "paused" ? event.reason ?? state.pauseReason : null,
          next: event.next ?? [],
        });
        return;
      }
      default:
        set({ events, lastEventId });
    }
  },
}));

function newThreadId() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now());
}

async function follow(stream: (signal: AbortSignal) => Promise<void>) {
  useRun.getState().controller?.abort();
  const controller = new AbortController();
  useRun.setState({ controller });
  useUi.getState().setRightTab("run");
  try {
    await stream(controller.signal);
    const status = useRun.getState().status;
    if (status === "running" || status === "queued") {
      useRun.setState({ controller: null });
    }
  } catch (err) {
    if ((err as Error).name === "AbortError") {
      useRun.setState({ controller: null });
      return;
    }
    const message = err instanceof ApiError || err instanceof Error ? err.message : String(err);
    useRun.setState({
      status: "error",
      controller: null,
      error: { kind: err instanceof ApiError && err.status === 409 ? "busy" : "error", message, fixes: [] },
    });
  }
}

function handler(event: RunEvent) {
  useRun.getState().handle(event);
}

/** Start a run of the flow open in the editor (unsaved edits included). */
export async function startRun(inputs: Record<string, unknown>, opts: { chatMessage?: string } = {}) {
  const { spec, flowId } = useFlow.getState();
  if (!spec) return;
  const run = useRun.getState();
  const standIn = useUi.getState().standIn;
  const breakpoints = useUi.getState().breakpoints;
  let threadId: string | undefined;
  if (opts.chatMessage !== undefined) {
    threadId = run.threadId ?? newThreadId();
    useRun.setState({
      ...empty,
      threadId,
      chat: [...run.chat, { role: "user", content: opts.chatMessage }, { role: "assistant", content: "", pending: true }],
    });
    inputs = { ...inputs, message: opts.chatMessage };
  } else {
    useRun.setState({ ...empty, lastInputs: inputs });
  }
  useRun.setState({ status: "queued" });
  await follow((signal) =>
    api.runStream(
      {
        flow_id: flowId ?? undefined,
        spec,
        inputs,
        thread_id: threadId,
        stand_in: standIn,
        pause_before: breakpoints.before,
        pause_after: breakpoints.after,
      },
      handler,
      signal,
    ),
  );
}

/** Stop the run: it ends where it is (its Save Points stay, so it can be continued). */
export async function stopRun() {
  const { runId, controller, status } = useRun.getState();
  if (runId && (status === "running" || status === "queued" || status === "paused")) {
    try {
      await api.cancelRun(runId);
      if (status === "paused") useRun.setState({ status: "cancelled", waiting: [], pauseReason: null });
      return;
    } catch {
      /* fall through to dropping the stream */
    }
  }
  controller?.abort();
}

/** Answer the waiting Ask a Human steps ({interrupt id: answer}) and follow the rest of the run. */
export async function answerWaiting(answers: Record<string, unknown>) {
  const { runId } = useRun.getState();
  if (!runId) return;
  useRun.setState({ status: "queued", waiting: [], pauseReason: null });
  await follow((signal) => api.resumeStream(runId, answers, handler, signal));
}

/** Carry on after a breakpoint, an error or a stop. */
export async function continueRun() {
  const { runId } = useRun.getState();
  if (!runId) return;
  useRun.setState({ status: "queued", waiting: [], pauseReason: null, error: null });
  await follow((signal) => api.continueStream(runId, handler, signal));
}

/** Re-run from a Save Point of the current run, with changed Flow Data. */
export async function forkFrom(checkpointId: string, update: Record<string, unknown> | null) {
  const { runId, threadId, chat } = useRun.getState();
  if (!runId) return;
  const breakpoints = useUi.getState().breakpoints;
  useRun.setState({ ...empty, threadId, chat, status: "queued" });
  await follow((signal) =>
    api.forkStream(
      runId,
      { checkpoint_id: checkpointId, update, pause_before: breakpoints.before, pause_after: breakpoints.after },
      handler,
      signal,
    ),
  );
}

/** Show a run that is already going or waiting (after a reload, or one started by a trigger). */
export async function attachRun(runId: string) {
  const detail = await api.run(runId);
  useRun.setState({ ...empty, lastInputs: detail.inputs ?? {} });
  for (const event of detail.events) useRun.getState().handle(event);
  if (detail.status === "paused" && detail.pending) {
    useRun.setState({ status: "paused", waiting: detail.pending.interrupts, pauseReason: detail.pending.reason as "ask_human" | "breakpoint", next: detail.pending.next });
  }
  if (detail.status === "running" || detail.status === "queued") {
    useRun.setState({ status: detail.status });
    await follow((signal) => api.followStream(runId, useRun.getState().lastEventId, handler, signal));
  }
}

/** Play a recorded run back on the canvas (Run Replay). */
export async function replayRun(events: RunEvent[], speed = 1) {
  useRun.setState({ ...empty, replaying: true });
  useUi.getState().setRightTab("run");
  let last = events[0]?.ts ?? 0;
  for (const event of events) {
    const wait = Math.min(Math.max((event.ts - last) / speed, 120), 900);
    last = event.ts;
    await new Promise((resolve) => setTimeout(resolve, event.type === "run_started" || event.type === "run_queued" ? 0 : wait));
    if (!useRun.getState().replaying) return;
    useRun.getState().handle(event);
  }
  useRun.setState({ replaying: false });
}
