import { describe, expect, it, vi } from "vitest";
import { EasyChainClient, EasyChainError, readSse, type AskRequest } from "./index";

function json(body: object): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

function sse(events: object[]): Response {
  const text = events.map((e, i) => `id: ${i + 1}\nevent: x\ndata: ${JSON.stringify(e)}\n\n`).join("");
  const bytes = new TextEncoder().encode(text);
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      // Split mid-event to prove the reader joins chunks.
      controller.enqueue(bytes.slice(0, 17));
      controller.enqueue(bytes.slice(17));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("EasyChainClient", () => {
  it("streams the events of a new run", async () => {
    const fetch = vi.fn().mockResolvedValue(
      sse([
        { type: "run_queued", run_id: "r1", ts: 1 },
        { type: "token", run_id: "r1", ts: 2, step: "ai", text: "Hel" },
        { type: "run_finished", run_id: "r1", ts: 3, status: "ok", output: { answer: "Hello" } },
      ]),
    );
    const client = new EasyChainClient({ baseUrl: "http://easy:8000/", fetch, headers: { "X-Team": "a" } });
    const seen = [];
    for await (const event of client.run({ flowId: "hello", inputs: { q: "hi" } })) seen.push(event.type);
    expect(seen).toEqual(["run_queued", "token", "run_finished"]);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("http://easy:8000/api/runs");
    expect(JSON.parse(init.body)).toMatchObject({ flow_id: "hello", inputs: { q: "hi" }, background: false });
    expect(init.headers["X-Team"]).toBe("a");
  });

  it("answers waiting steps and reports API errors plainly", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(sse([{ type: "run_finished", run_id: "r1", ts: 1, status: "ok" }]))
      .mockResolvedValueOnce(new Response(JSON.stringify({ message: "This conversation is still busy.", kind: "busy" }), { status: 409 }));
    const client = new EasyChainClient({ fetch });
    for await (const _ of client.resume("r1", { i1: { action: "approve" } })) void _;
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ answers: { i1: { action: "approve" } } });
    await expect(client.start({ flowId: "chat", threadId: "t" })).rejects.toMatchObject({
      name: "EasyChainError",
      status: 409,
      kind: "busy",
      message: "This conversation is still busy.",
    } satisfies Partial<EasyChainError>);
  });

  it("waits for a background run across reconnects", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json({ status: "queued", events: [] }))
      .mockResolvedValueOnce(sse([{ type: "run_started", run_id: "r9", ts: 1, event_id: 4 }]))
      .mockResolvedValueOnce(json({ status: "running", events: [{ type: "run_started", run_id: "r9", ts: 1, event_id: 4 }] }))
      .mockResolvedValueOnce(sse([{ type: "run_finished", run_id: "r9", ts: 2, event_id: 7, status: "ok" }]));
    const client = new EasyChainClient({ fetch });
    const final = await client.wait("r9");
    expect(final.status).toBe("ok");
    expect(fetch.mock.calls.map((c) => c[0])).toEqual(["/api/runs/r9", "/api/runs/r9/events?after=0", "/api/runs/r9", "/api/runs/r9/events?after=4"]);
  });

  it("waits for the newest part of a run that was answered and carried on", async () => {
    const earlier = [
      { type: "run_started", run_id: "r1", ts: 1, event_id: 2 },
      { type: "run_finished", run_id: "r1", ts: 2, event_id: 5, status: "paused", reason: "ask_human", interrupts: [] },
      { type: "run_queued", run_id: "r1", ts: 3, event_id: 6 },
    ];
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json({ status: "running", events: earlier }))
      .mockResolvedValueOnce(
        sse([
          { type: "run_started", run_id: "r1", ts: 4, event_id: 7 },
          { type: "run_finished", run_id: "r1", ts: 5, event_id: 9, status: "ok", output: { sent: true } },
        ]),
      );
    const client = new EasyChainClient({ fetch });
    const final = await client.wait("r1");
    expect(final).toMatchObject({ status: "ok", output: { sent: true } });
    expect(fetch.mock.calls[1][0]).toBe("/api/runs/r1/events?after=5");
  });

  it("returns a paused run's pause straight away", async () => {
    const paused = { type: "run_finished", run_id: "r1", ts: 2, event_id: 5, status: "paused", reason: "ask_human", interrupts: [{ id: "i1" }] };
    const fetch = vi.fn().mockResolvedValueOnce(json({ status: "paused", events: [{ type: "run_started", run_id: "r1", ts: 1, event_id: 2 }, paused] }));
    const client = new EasyChainClient({ fetch });
    expect(await client.wait("r1")).toEqual(paused);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("closes the event stream when you stop reading it", async () => {
    const cancel = vi.fn();
    const enc = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(enc.encode(`data: ${JSON.stringify({ type: "run_started", run_id: "r1", ts: 1 })}\n\n`));
        // ...and the run goes on: the stream stays open.
      },
      cancel,
    });
    const fetch = vi.fn().mockResolvedValueOnce(new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
    const client = new EasyChainClient({ fetch });
    for await (const event of client.follow("r1")) {
      expect(event.type).toBe("run_started");
      break;
    }
    expect(cancel).toHaveBeenCalled();
    expect((fetch.mock.calls[0][1].signal as AbortSignal).aborted).toBe(true);
  });

  it("types tool approvals", () => {
    const request: AskRequest = { kind: "approve_tool", question: "Use send_email?", actions: [{ tool: "send_email", args: { to: "a@b.c" } }], allowed: ["approve", "reject"] };
    expect(request.actions?.[0].tool).toBe("send_email");
  });
});

describe("readSse", () => {
  it("cancels the body when the reader stops early", async () => {
    const cancel = vi.fn();
    const enc = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(enc.encode("data: 1\n\ndata: 2\n\n"));
      },
      cancel,
    });
    for await (const data of readSse(body)) {
      expect(data).toBe("1");
      break;
    }
    expect(cancel).toHaveBeenCalled();
  });
});
