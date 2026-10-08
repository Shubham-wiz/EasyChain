import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EasyChainClient } from "./index";
import { RECONNECT_DELAYS, useEasyChainRun } from "./react";

function sse(events: object[]): Response {
  const text = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
  return new Response(text, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("useEasyChainRun", () => {
  it("tracks a run that waits for a person and finishes after the answer", async () => {
    const waiting = { id: "i1", step: "check", path: [], request: { kind: "approve", question: "Send?" } };
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        sse([
          { type: "run_queued", run_id: "r1", ts: 1 },
          { type: "run_started", run_id: "r1", ts: 2 },
          { type: "token", run_id: "r1", ts: 3, step: "draft", text: "Hi " },
          { type: "token", run_id: "r1", ts: 4, step: "draft", text: "there" },
          { type: "step_finished", run_id: "r1", ts: 5, step: "draft", output: {} },
          { type: "step_paused", run_id: "r1", ts: 6, step: "check" },
          { type: "run_finished", run_id: "r1", ts: 7, status: "paused", reason: "ask_human", interrupts: [waiting] },
        ]),
      )
      .mockResolvedValueOnce(
        sse([
          { type: "run_started", run_id: "r1", ts: 8 },
          { type: "run_finished", run_id: "r1", ts: 9, status: "ok", output: { sent: true } },
        ]),
      );
    const client = new EasyChainClient({ fetch });
    const { result } = renderHook(() => useEasyChainRun(client, { flowId: "approve" }));
    await act(() => result.current.start({ draft: "x" }));
    await waitFor(() => expect(result.current.status).toBe("paused"));
    expect(result.current.text).toBe("Hi there");
    expect(result.current.steps).toEqual({ draft: "done", check: "waiting" });
    expect(result.current.waiting).toEqual([waiting]);
    await act(() => result.current.answer({ i1: { action: "approve" } }));
    await waitFor(() => expect(result.current.status).toBe("ok"));
    expect(result.current.output).toEqual({ sent: true });
    expect(fetch.mock.calls[1][0]).toBe("/api/runs/r1/resume");
  });

  describe("when the event stream drops", () => {
    beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
    afterEach(() => vi.useRealTimers());

    it("picks the run up again from the last event", async () => {
      const fetch = vi
        .fn()
        .mockResolvedValueOnce(
          sse([
            { type: "run_queued", run_id: "r1", ts: 1, event_id: 1 },
            { type: "run_started", run_id: "r1", ts: 2, event_id: 2 },
            { type: "token", run_id: "r1", ts: 3, event_id: 3, step: "draft", text: "Hi " },
          ]),
        )
        .mockResolvedValueOnce(
          sse([
            { type: "token", run_id: "r1", ts: 4, event_id: 4, step: "draft", text: "there" },
            { type: "run_finished", run_id: "r1", ts: 5, event_id: 5, status: "ok", output: { reply: "Hi there" } },
          ]),
        );
      const client = new EasyChainClient({ fetch });
      const { result } = renderHook(() => useEasyChainRun(client, { flowId: "reply" }));
      await act(async () => {
        const started = result.current.start({});
        await vi.advanceTimersByTimeAsync(RECONNECT_DELAYS[0]);
        await started;
      });
      expect(result.current.status).toBe("ok");
      expect(result.current.text).toBe("Hi there");
      expect(fetch.mock.calls[1][0]).toBe("/api/runs/r1/events?after=3");
    });

    it("says so after a few tries", async () => {
      const fetch = vi.fn().mockResolvedValueOnce(sse([{ type: "run_queued", run_id: "r1", ts: 1, event_id: 1 }]));
      fetch.mockImplementation(async () => sse([]));
      const client = new EasyChainClient({ fetch });
      const { result } = renderHook(() => useEasyChainRun(client, { flowId: "reply" }));
      await act(async () => {
        const started = result.current.start({});
        await vi.advanceTimersByTimeAsync(RECONNECT_DELAYS.reduce((a, b) => a + b, 0));
        await started;
      });
      expect(fetch).toHaveBeenCalledTimes(1 + RECONNECT_DELAYS.length);
      expect(result.current.status).toBe("error");
      expect(result.current.error).toMatchObject({ kind: "disconnected", message: "Lost the connection to the run." });
    });
  });
});
