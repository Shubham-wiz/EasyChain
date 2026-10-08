import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../lib/api";
import type { FlowSpec, RunEvent } from "../lib/types";
import { useFlow } from "./flow";
import { RECONNECT_DELAYS, attachRun, replayRun, startRun, stopRun, tryRun, useRun } from "./run";
import { useUi } from "./ui";

vi.mock("../lib/api", async (original) => ({
  ...(await original<typeof import("../lib/api")>()),
  api: { runStream: vi.fn(), followStream: vi.fn(), run: vi.fn(), catalog: vi.fn(), cancelRun: vi.fn() },
}));

type OnEvent = (event: RunEvent) => void;

const mocked = api as unknown as Record<"runStream" | "followStream" | "run" | "catalog" | "cancelRun", ReturnType<typeof vi.fn>>;

const spec: FlowSpec = {
  version: 1,
  name: "Try",
  description: "",
  data: [],
  steps: [
    { id: "input", type: "input", name: "Input", description: "", settings: { mode: "form", fields: [{ name: "question", example: "Hi?" }] } },
    { id: "ai", type: "ai_model", name: "AI", description: "", settings: { model: "anthropic:claude" } },
  ],
  connections: [],
  canvas: { steps: {}, notes: [] },
};

const ev = (type: string, extra: Record<string, unknown> = {}) => ({ type, run_id: "r1", ts: 1, ...extra }) as unknown as RunEvent;

/** A stream the test drives by hand: send events, then end or fail it. */
function manual() {
  let onEvent: OnEvent = () => undefined;
  let signal: AbortSignal | null = null;
  let end: () => void = () => undefined;
  let fail: (err: unknown) => void = () => undefined;
  const impl = (...args: unknown[]) => {
    onEvent = args.find((a) => typeof a === "function") as OnEvent;
    signal = args.find((a) => a instanceof AbortSignal) as AbortSignal;
    return new Promise<void>((resolve, reject) => {
      end = resolve;
      fail = reject;
    });
  };
  return {
    impl,
    send: (event: RunEvent) => onEvent(event),
    end: () => end(),
    fail: (err: unknown) => fail(err),
    get signal() {
      return signal!;
    },
  };
}

const aborted = () => Object.assign(new Error("aborted"), { name: "AbortError" });

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  useFlow.getState().load("f1", spec);
  useUi.getState().loadFlowPrefs("f1");
  useRun.getState().newChat();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("run streams belong to the panel that started them", () => {
  it("opening another flow stops the old run's stream from showing up", async () => {
    const a = manual();
    mocked.runStream.mockImplementationOnce(a.impl);
    const done = startRun({ question: "x" });
    a.send(ev("run_queued", { event_id: 1 }));
    expect(useRun.getState().runId).toBe("r1");

    useRun.getState().newChat(); // what opening another flow does
    expect(a.signal.aborted).toBe(true);
    a.send(ev("run_finished", { status: "ok", output: { answer: "old" } }));
    a.fail(aborted());
    await done;
    expect(useRun.getState()).toMatchObject({ status: "idle", runId: null, final: null, controller: null });
  });

  it("an aborted stream ending late leaves the next stream in charge", async () => {
    const a = manual();
    const b = manual();
    mocked.runStream.mockImplementationOnce(a.impl).mockImplementationOnce(b.impl);
    const first = startRun({ question: "a" });
    const second = startRun({ question: "b" });
    expect(a.signal.aborted).toBe(true);
    a.fail(aborted());
    await first;
    const controller = useRun.getState().controller;
    expect(controller).not.toBeNull();
    expect(controller!.signal).toBe(b.signal);

    // The second stream can still be stopped.
    useRun.setState({ runId: null });
    await stopRun();
    expect(b.signal.aborted).toBe(true);
    b.fail(aborted());
    await second;
  });

  it("a second Replay stops the first", async () => {
    vi.useFakeTimers();
    const first = replayRun([ev("run_started", { stand_in: false, thread_id: "t" }), ev("step_started", { step: "old", ts: 2 }), ev("step_finished", { step: "old", ts: 3 })]);
    await vi.advanceTimersByTimeAsync(0);
    const second = replayRun([ev("run_started", { stand_in: false, thread_id: "t" }), ev("step_started", { step: "new", ts: 2 })]);
    await vi.advanceTimersByTimeAsync(5000);
    await Promise.all([first, second]);
    expect(useRun.getState().order).toEqual(["new"]);
    expect(useRun.getState()).toMatchObject({ replaying: false, controller: null });
  });

  it("a replay stops a live run's stream", async () => {
    vi.useFakeTimers();
    const a = manual();
    mocked.runStream.mockImplementationOnce(a.impl);
    const run = startRun({ question: "x" });
    const replay = replayRun([ev("run_started", { stand_in: false, thread_id: "t" })]);
    expect(a.signal.aborted).toBe(true);
    a.send(ev("step_started", { step: "live" }));
    a.fail(aborted());
    await vi.advanceTimersByTimeAsync(1000);
    await Promise.all([run, replay]);
    expect(useRun.getState().order).toEqual([]);
  });

  it("a run that loads after another flow opened is not shown", async () => {
    let answer: (v: unknown) => void = () => undefined;
    mocked.run.mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
    const attach = attachRun("r1");
    useRun.getState().newChat();
    answer({ run_id: "r1", status: "paused", inputs: {}, events: [ev("run_finished", { status: "paused" })], pending: null });
    await attach;
    expect(useRun.getState().status).toBe("idle");
  });
});

describe("a dropped event stream", () => {
  it("picks the run up again from the last event it saw", async () => {
    vi.useFakeTimers();
    mocked.runStream.mockImplementationOnce(async (_body: unknown, onEvent: OnEvent) => {
      onEvent(ev("run_queued", { event_id: 1 }));
      onEvent(ev("run_started", { event_id: 2, stand_in: false, thread_id: "t" }));
      onEvent(ev("token", { event_id: 3, step: "ai", text: "Hel" }));
    });
    mocked.followStream.mockImplementationOnce(async (_id: string, _after: number, onEvent: OnEvent) => {
      onEvent(ev("token", { event_id: 4, step: "ai", text: "lo" }));
      onEvent(ev("run_finished", { event_id: 5, status: "ok", output: { answer: "Hello" } }));
    });
    const done = startRun({ question: "x" });
    await vi.advanceTimersByTimeAsync(RECONNECT_DELAYS[0]);
    await done;
    expect(mocked.followStream).toHaveBeenCalledTimes(1);
    expect(mocked.followStream.mock.calls[0].slice(0, 2)).toEqual(["r1", 3]);
    expect(useRun.getState()).toMatchObject({ status: "ok", controller: null, error: null });
    expect(useRun.getState().steps.ai.tokens).toBe("Hello");
  });

  it("reconnects after a network error too", async () => {
    vi.useFakeTimers();
    mocked.runStream.mockImplementationOnce(async (_body: unknown, onEvent: OnEvent) => {
      onEvent(ev("run_queued", { event_id: 1 }));
      throw new TypeError("network error");
    });
    mocked.followStream.mockImplementationOnce(async (_id: string, _after: number, onEvent: OnEvent) => {
      onEvent(ev("run_finished", { event_id: 2, status: "ok" }));
    });
    const done = startRun({ question: "x" });
    await vi.advanceTimersByTimeAsync(RECONNECT_DELAYS[0]);
    await done;
    expect(useRun.getState().status).toBe("ok");
  });

  it("says the connection was lost after a few tries, with a way to reconnect", async () => {
    vi.useFakeTimers();
    mocked.runStream.mockImplementationOnce(async (_body: unknown, onEvent: OnEvent) => {
      onEvent(ev("run_queued", { event_id: 1 }));
    });
    mocked.followStream.mockResolvedValue(undefined);
    const done = startRun({ question: "x" });
    await vi.advanceTimersByTimeAsync(RECONNECT_DELAYS.reduce((a, b) => a + b, 0));
    await done;
    expect(mocked.followStream).toHaveBeenCalledTimes(RECONNECT_DELAYS.length);
    const { status, error, controller } = useRun.getState();
    expect(status).toBe("error");
    expect(controller).toBeNull();
    expect(error).toMatchObject({ kind: "disconnected", message: "Lost the connection to this run." });
    expect(error!.fixes).toEqual([{ kind: "reconnect", label: "Reconnect", params: { run_id: "r1" } }]);
  });

  it("does not retry when the server refused the run", async () => {
    mocked.runStream.mockRejectedValueOnce(new ApiError(409, "This conversation is still busy."));
    await startRun({ question: "x" });
    expect(mocked.followStream).not.toHaveBeenCalled();
    expect(useRun.getState().error).toMatchObject({ kind: "busy", message: "This conversation is still busy." });
  });
});

describe("Try it", () => {
  it("uses the stand-in AI for this flow only when a key is missing", async () => {
    mocked.catalog.mockResolvedValueOnce({ providers: [{ id: "anthropic", key_set: false }] });
    const order: string[] = [];
    mocked.runStream.mockImplementationOnce(async () => void order.push("run"));
    await tryRun("f1", spec, () => order.push("started"));
    expect(order).toEqual(["started", "run"]);
    expect(mocked.runStream.mock.calls[0][0]).toMatchObject({ stand_in: true, inputs: { question: "Hi?" } });
    expect(useUi.getState().standIn).toBe(true);
    expect(localStorage.getItem("easychain.standIn")).not.toBe("true");

    // Other flows keep the user's own setting; this one keeps its stand-in.
    useUi.getState().loadFlowPrefs("f2");
    expect(useUi.getState().standIn).toBe(false);
    useUi.getState().loadFlowPrefs("f1");
    expect(useUi.getState().standIn).toBe(true);
    // The switch still works as before, and drops the flow's own setting.
    useUi.getState().setStandIn(false);
    useUi.getState().loadFlowPrefs("f1");
    expect(useUi.getState().standIn).toBe(false);
  });

  it("does nothing when another flow opened meanwhile", async () => {
    let answer: (v: unknown) => void = () => undefined;
    mocked.catalog.mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
    const started = vi.fn();
    const trying = tryRun("f1", spec, started);
    useFlow.getState().load("f2", spec);
    answer({ providers: [] });
    await trying;
    expect(started).not.toHaveBeenCalled();
    expect(mocked.runStream).not.toHaveBeenCalled();
  });
});
