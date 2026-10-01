import { describe, expect, it, vi } from "vitest";
import { EasyChainClient, EasyChainError } from "./index";

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
      .mockResolvedValueOnce(sse([{ type: "run_started", run_id: "r9", ts: 1, event_id: 4 }]))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: "running" }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(sse([{ type: "run_finished", run_id: "r9", ts: 2, event_id: 7, status: "ok" }]));
    const client = new EasyChainClient({ fetch });
    const final = await client.wait("r9");
    expect(final.status).toBe("ok");
    expect(fetch.mock.calls[2][0]).toBe("/api/runs/r9/events?after=4");
  });
});
