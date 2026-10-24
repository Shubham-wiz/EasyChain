import { describe, expect, it } from "vitest";
import { newRow, toExpect, toRows } from "./checks";

describe("a case's checks as rows", () => {
  it("round-trips what people write by hand", () => {
    const expect_ = {
      status: "ok",
      "output.answer": { contains: ["refund", "14 days"], max_length: 600 },
      "output.category": "Billing",
      "output.order": { schema: { type: "object", required: ["id"] } },
      tools: ["find_order"],
      not_tools: ["refund"],
      trajectory: ["find_order", "reply"],
      tool_calls: { max: 4 },
      route: { triage: "Billing", check: "Approved" },
      max_seconds: 20,
      max_cost: 0.02,
      judge: { rubric: "Polite.", pass_score: 0.8 },
    };
    const rows = toRows(expect_);
    expect(rows.filter((r) => r.kind === "value").map((r) => [r.path, r.rule])).toEqual([
      ["output.answer", "contains"],
      ["output.answer", "max_length"],
      ["output.category", "equals"],
      ["output.order", "schema"],
    ]);
    expect(toExpect(rows)).toEqual(expect_);
  });

  it("keeps what it doesn't edit, and reads typed values sensibly", () => {
    const kept = { files: { "/report.md": { contains: "Bees" } }, judge: { rubric: "x", model: "openai:gpt-4.1" }, tool_calls: 2 };
    expect(toExpect(toRows(kept))).toEqual(kept);
    expect(toExpect([{ kind: "value", path: "output.size", rule: "equals", value: "4" }])).toEqual({ "output.size": 4 });
    expect(toExpect([{ kind: "value", path: "output.text", rule: "equals", value: "four" }])).toEqual({ "output.text": "four" });
    expect(toExpect([{ kind: "value", path: "output.kind", rule: "one_of", value: "A, B" }])).toEqual({ "output.kind": { one_of: ["A", "B"] } });
    expect(toExpect([{ kind: "value", path: "output.x", rule: "not_empty", value: "" }])).toEqual({ "output.x": { not_empty: true } });
    expect(toExpect([{ kind: "trajectory", list: "a, b", order: "exact" }])).toEqual({ trajectory: { tools: ["a", "b"], order: "exact" } });
    expect(toExpect([newRow("judge")])).toEqual({ judge: { rubric: "", pass_score: 0.7 } });
  });
});
