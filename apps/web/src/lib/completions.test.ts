import { describe, expect, it } from "vitest";
import { flowDataCompletions } from "./completions";

const fields = [{ name: "customer" }, { name: "order_id" }];

/** The line after accepting the first suggestion with the cursor between `before` and `after`. */
function accept(before: string, after = "") {
  const [s] = flowDataCompletions(before, after, fields);
  if (!s) return null;
  return { label: s.label, line: before.slice(0, before.length - s.before) + s.label + after.slice(s.after) };
}

describe("Flow Data completions", () => {
  it("complete the key after data[ to exactly what the label says", () => {
    expect(accept("x = data[", "]")).toEqual({ label: 'data["customer"]', line: 'x = data["customer"]' });
    expect(accept("x = data[")).toEqual({ label: 'data["customer"]', line: 'x = data["customer"]' });
    expect(accept('x = data["cu', '"]')).toEqual({ label: 'data["customer"]', line: 'x = data["customer"]' });
    expect(accept("x = data['", "']  # note")).toEqual({ label: "data['customer']", line: "x = data['customer']  # note" });
  });

  it("complete data.get( as a call", () => {
    expect(accept('n = data.get("', '")')).toEqual({ label: 'data.get("customer")', line: 'n = data.get("customer")' });
    expect(accept("n = data.get(", ")")).toEqual({ label: 'data.get("customer")', line: 'n = data.get("customer")' });
  });

  it("write data[...] while typing a word", () => {
    expect(accept("    return dat")).toEqual({ label: 'data["customer"]', line: '    return data["customer"]' });
    expect(flowDataCompletions("    ", "", fields)).toHaveLength(2);
  });

  it("offer nothing after other quotes, brackets and attributes", () => {
    expect(accept('x = "')).toBeNull();
    expect(accept("rows[")).toBeNull();
    expect(accept("mydata[")).toBeNull();
    expect(accept("self.da")).toBeNull();
    expect(accept("print(")).toBeNull();
  });
});
