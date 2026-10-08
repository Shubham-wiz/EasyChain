// useEasyChainRun: run a flow from a React app and render what happens as it happens.
//
//   const easy = new EasyChainClient({ baseUrl: "http://localhost:8000" });
//   function Summary({ url }) {
//     const run = useEasyChainRun(easy, { flowId: "summarise-url" });
//     return <>
//       <button onClick={() => run.start({ url })}>Summarise</button>
//       <p>{run.text}</p>
//       {run.waiting.map((w) => <Approve key={w.id} onYes={() => run.answer({ [w.id]: { action: "approve" } })} />)}
//     </>;
//   }

import { useCallback, useEffect, useRef, useState } from "react";
import { EasyChainError, type EasyChainClient } from "./index.js";
import type { Answer, RunError, RunEvent, RunStatus, Waiting } from "./types.js";

/** How long to wait (ms) before each try at picking up a run whose event stream dropped. */
export const RECONNECT_DELAYS = [500, 1000, 2000, 4000];

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener("abort", done);
  });
}

export interface UseRunOptions {
  flowId: string;
  threadId?: string;
  standIn?: boolean;
}

export interface UseRunResult {
  status: "idle" | RunStatus;
  runId: string | null;
  events: RunEvent[];
  /** Text streamed by AI steps so far, by step id. */
  tokens: Record<string, string>;
  /** All streamed text so far (handy for single-answer flows). */
  text: string;
  /** Step id -> "running" | "done" | "error" | "waiting". */
  steps: Record<string, string>;
  output: Record<string, unknown> | null;
  error: RunError | null;
  waiting: Waiting[];
  start: (inputs?: Record<string, unknown>) => Promise<void>;
  answer: (answers: Record<string, Answer | unknown>) => Promise<void>;
  cancel: () => Promise<void>;
  reset: () => void;
}

export function useEasyChainRun(client: EasyChainClient, options: UseRunOptions): UseRunResult {
  const [status, setStatus] = useState<UseRunResult["status"]>("idle");
  const [runId, setRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [tokens, setTokens] = useState<Record<string, string>>({});
  const [steps, setSteps] = useState<Record<string, string>>({});
  const [output, setOutput] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<RunError | null>(null);
  const [waiting, setWaiting] = useState<Waiting[]>([]);
  const abort = useRef<AbortController | null>(null);
  const runRef = useRef<string | null>(null);
  // The last event seen, to pick the run up again from there if the stream drops.
  const lastId = useRef(0);

  useEffect(() => () => abort.current?.abort(), []);

  const handle = useCallback((event: RunEvent) => {
    if (event.event_id != null) lastId.current = Math.max(lastId.current, event.event_id);
    if (event.type !== "token") setEvents((list) => [...list, event]);
    if (event.path?.length) return; // steps inside Sub-flows
    switch (event.type) {
      case "run_queued":
        runRef.current = event.run_id;
        setRunId(event.run_id);
        setStatus((s) => (s === "running" ? s : "queued"));
        break;
      case "run_started":
        runRef.current = event.run_id;
        setRunId(event.run_id);
        setStatus("running");
        setWaiting([]);
        break;
      case "token":
        if (event.step) setTokens((t) => ({ ...t, [event.step!]: (t[event.step!] ?? "") + (event.text ?? "") }));
        break;
      case "step_started":
        if (event.step) setSteps((s) => ({ ...s, [event.step!]: "running" }));
        break;
      case "step_finished":
        if (event.step) setSteps((s) => ({ ...s, [event.step!]: "done" }));
        break;
      case "step_failed":
        if (event.step) setSteps((s) => ({ ...s, [event.step!]: "error" }));
        break;
      case "step_paused":
        if (event.step) setSteps((s) => ({ ...s, [event.step!]: "waiting" }));
        break;
      case "run_finished":
        setStatus(event.status ?? "ok");
        setOutput(event.output ?? null);
        setError(event.error ?? null);
        setWaiting(event.status === "paused" ? event.interrupts ?? [] : []);
        break;
    }
  }, []);

  // Follow a stream until this part of the run ends. If it ends or breaks before that, pick
  // the run up again from the last event seen (a few times, waiting longer each time), then
  // report that the connection was lost.
  const follow = useCallback(
    async (stream: (signal: AbortSignal) => AsyncGenerator<RunEvent>) => {
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      let open = stream;
      let attempt = 0;
      for (;;) {
        const seen = lastId.current;
        let ended = false;
        let failure: unknown = null;
        try {
          for await (const event of open(controller.signal)) {
            handle(event);
            if (event.type === "run_finished") ended = true;
          }
        } catch (err) {
          failure = err;
        }
        if (abort.current !== controller || controller.signal.aborted || ended) return;
        const id = runRef.current;
        if (failure instanceof EasyChainError || (failure != null && !id)) {
          setStatus("error");
          setError({ kind: failure instanceof EasyChainError && failure.kind ? failure.kind : "error", message: failure instanceof Error ? failure.message : String(failure) });
          return;
        }
        if (lastId.current > seen) attempt = 0;
        if (!id || attempt >= RECONNECT_DELAYS.length) {
          setStatus("error");
          setError({
            kind: "disconnected",
            message: "Lost the connection to the run.",
            hint: "It may still be going on the server; follow it again with client.follow(runId).",
          });
          return;
        }
        await sleep(RECONNECT_DELAYS[attempt++], controller.signal);
        open = (signal) => client.follow(id, lastId.current, signal);
      }
    },
    [client, handle],
  );

  const reset = useCallback(() => {
    abort.current?.abort();
    runRef.current = null;
    lastId.current = 0;
    setStatus("idle");
    setRunId(null);
    setEvents([]);
    setTokens({});
    setSteps({});
    setOutput(null);
    setError(null);
    setWaiting([]);
  }, []);

  const start = useCallback(
    async (inputs: Record<string, unknown> = {}) => {
      reset();
      setStatus("queued");
      await follow((signal) => client.run({ flowId: options.flowId, threadId: options.threadId, standIn: options.standIn, inputs }, signal));
    },
    [client, follow, options.flowId, options.threadId, options.standIn, reset],
  );

  const answer = useCallback(
    async (answers: Record<string, Answer | unknown>) => {
      const id = runRef.current;
      if (!id) return;
      setWaiting([]);
      setStatus("queued");
      await follow((signal) => client.resume(id, answers, signal));
    },
    [client, follow],
  );

  const cancel = useCallback(async () => {
    if (runRef.current) await client.cancel(runRef.current);
  }, [client]);

  return {
    status,
    runId,
    events,
    tokens,
    text: Object.values(tokens).join(""),
    steps,
    output,
    error,
    waiting,
    start,
    answer,
    cancel,
    reset,
  };
}
