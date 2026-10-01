import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EasyChainClient } from "./index";
import { useEasyChainRun } from "./react";

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
});
