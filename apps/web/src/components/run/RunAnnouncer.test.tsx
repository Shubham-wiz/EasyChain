import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { RunEvent } from "../../lib/types";
import { useRun } from "../../state/run";
import { RunAnnouncer } from "./RunPanel";

const ev = (type: string, extra: Record<string, unknown> = {}) => ({ type, run_id: "r1", ts: 1, ...extra }) as unknown as RunEvent;

afterEach(() => {
  cleanup();
  useRun.getState().newChat();
});

describe("what screen readers hear about a run", () => {
  it("the status as it changes, not every streamed token", () => {
    render(<RunAnnouncer chat={false} />);
    const live = screen.getByRole("status");
    expect(live).toHaveTextContent("");
    act(() => {
      useRun.getState().handle(ev("run_started", { thread_id: "t", stand_in: false }));
      useRun.getState().handle(ev("token", { step: "ai", text: "Once upon " }));
      useRun.getState().handle(ev("token", { step: "ai", text: "a time" }));
    });
    expect(live).toHaveTextContent(/^Running\.$/);
    act(() => useRun.getState().handle(ev("run_finished", { status: "ok", output: { story: "Once upon a time" } })));
    expect(live).toHaveTextContent("Finished. The result is below.");
  });

  it("the chat reply once it is complete", () => {
    useRun.setState({ chat: [{ role: "user", content: "Hi" }, { role: "assistant", content: "", pending: true }] });
    render(<RunAnnouncer chat />);
    const live = screen.getByRole("status");
    act(() => {
      useRun.getState().handle(ev("run_started", { thread_id: "t", stand_in: false }));
      useRun.getState().handle(ev("token", { step: "reply", text: "Hello" }));
    });
    expect(live).toHaveTextContent(/^Running\.$/);
    act(() => useRun.getState().handle(ev("run_finished", { status: "ok", reply: "Hello there!" })));
    expect(live).toHaveTextContent("Reply: Hello there!");
  });
});
