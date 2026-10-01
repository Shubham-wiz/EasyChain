import { describe, expect, it } from "vitest";
import { readSse } from "./api";
import { formatCost, formatMs, preview } from "./utils";

function stream(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
}

describe("readSse", () => {
  it("parses events split across chunks", async () => {
    const got: string[] = [];
    await readSse(stream(['event: a\ndata: {"x"', ": 1}\n\nevent: b\r\ndata: 2\r\n\r\n", "data: 3"]), (d) => got.push(d));
    expect(got).toEqual(['{"x": 1}', "2", "3"]);
  });
});

describe("formatting", () => {
  it("formats durations, costs and previews", () => {
    expect(formatMs(0.2)).toBe("<1 ms");
    expect(formatMs(250)).toBe("250 ms");
    expect(formatMs(2500)).toBe("2.5 s");
    expect(formatCost(0)).toBe("$0");
    expect(formatCost(0.00001)).toBe("<$0.0001");
    expect(formatCost(0.0042)).toBe("$0.0042");
    expect(preview([{ role: "user", content: "hi" }])).toBe("user: hi");
    expect(preview("x".repeat(200), 10)).toHaveLength(10);
  });
});
