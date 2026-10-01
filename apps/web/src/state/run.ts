import { create } from "zustand";
import { api } from "../lib/api";
import type { Issue, RunError, RunEvent, Usage } from "../lib/types";
import { useCheck } from "./check";
import { useFlow } from "./flow";
import { useUi } from "./ui";

export type StepStatus = "running" | "done" | "error";

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
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
  failed?: boolean;
}

type Finished = Extract<RunEvent, { type: "run_finished" }>;

interface RunState {
  runId: string | null;
  status: "idle" | "running" | "ok" | "error";
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
};

/** Steps whose AI reply is the chat answer (they save into `messages`). */
function chatReplySteps(): Set<string> {
  const writes = useCheck.getState().analysis?.writes ?? {};
  return new Set(Object.entries(writes).filter(([, w]) => "messages" in w).map(([id]) => id));
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
    switch (event.type) {
      case "run_started":
        set({ runId: event.run_id, status: "running", standIn: event.stand_in, events, threadId: event.thread_id });
        return;
      case "step_started": {
        const steps = { ...state.steps, [event.step]: { status: "running" as const, tokens: "", input: event.input } };
        set({ steps, order: state.order.includes(event.step) ? state.order : [...state.order, event.step], events });
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
        set({ steps, chat });
        return;
      }
      case "step_finished": {
        const prev = state.steps[event.step] ?? { status: "running" as const, tokens: "" };
        set({
          steps: {
            ...state.steps,
            [event.step]: {
              ...prev,
              status: "done",
              output: event.output,
              durationMs: event.duration_ms,
              usage: event.usage,
              cost: event.cost,
              model: event.model,
            },
          },
          order: state.order.includes(event.step) ? state.order : [...state.order, event.step],
          events,
        });
        return;
      }
      case "route": {
        const prev = state.steps[event.step];
        if (prev) set({ steps: { ...state.steps, [event.step]: { ...prev, exit: event.exit } }, events });
        return;
      }
      case "step_failed": {
        const prev = state.steps[event.step] ?? { tokens: "" };
        set({ steps: { ...state.steps, [event.step]: { ...prev, status: "error", error: event.error } }, events });
        return;
      }
      case "run_finished": {
        let chat = state.chat;
        if (chat.length && chat[chat.length - 1].pending) {
          const last = chat[chat.length - 1];
          chat =
            event.status === "ok"
              ? [...chat.slice(0, -1), { role: "assistant", content: event.reply ?? last.content }]
              : [...chat.slice(0, -1), { role: "assistant", content: event.error?.message ?? "The run failed.", failed: true }];
        }
        set({
          status: event.status,
          final: event,
          error: event.error ?? null,
          issues: event.issues ?? [],
          events,
          chat,
          controller: null,
        });
        return;
      }
    }
  },
}));

function newThreadId() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now());
}

/** Start a run of the flow open in the editor (unsaved edits included). */
export async function startRun(inputs: Record<string, unknown>, opts: { chatMessage?: string } = {}) {
  const { spec, flowId } = useFlow.getState();
  if (!spec) return;
  const run = useRun.getState();
  run.controller?.abort();
  const controller = new AbortController();
  const standIn = useUi.getState().standIn;
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
  useRun.setState({ status: "running", controller });
  useUi.getState().setRightTab("run");
  try {
    await api.runStream(
      { flow_id: flowId ?? undefined, spec, inputs, thread_id: threadId, stand_in: standIn },
      (event) => useRun.getState().handle(event),
      controller.signal,
    );
    if (useRun.getState().status === "running") {
      useRun.setState({ status: "error", error: { kind: "error", message: "The run stopped unexpectedly.", fixes: [] } });
    }
  } catch (err) {
    if ((err as Error).name === "AbortError") {
      useRun.setState({ status: "idle", controller: null });
      return;
    }
    useRun.setState({
      status: "error",
      controller: null,
      error: { kind: "error", message: err instanceof Error ? err.message : String(err), fixes: [] },
    });
  }
}

export function stopRun() {
  useRun.getState().controller?.abort();
}

/** Play a recorded run back on the canvas (Run Replay). */
export async function replayRun(events: RunEvent[], speed = 1) {
  useRun.setState({ ...empty, replaying: true });
  useUi.getState().setRightTab("run");
  let last = events[0]?.ts ?? 0;
  for (const event of events) {
    const wait = Math.min(Math.max((event.ts - last) / speed, 120), 900);
    last = event.ts;
    await new Promise((resolve) => setTimeout(resolve, event.type === "run_started" ? 0 : wait));
    if (!useRun.getState().replaying) return;
    useRun.getState().handle(event);
  }
  useRun.setState({ replaying: false });
}
