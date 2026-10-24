// A Test Set case's `expect:` as rows people can edit, and back (python/src/easychain/checks.py
// has the same checks). What the editor doesn't know is kept as a "raw" row, unchanged.

export const VALUE_RULES = ["equals", "contains", "not_contains", "matches", "not_empty", "max_length", "min_length", "count", "schema", "at_least", "at_most", "one_of"] as const;
export type ValueRule = (typeof VALUE_RULES)[number];

export const RULE_LABELS: Record<ValueRule, string> = {
  equals: "is exactly",
  contains: "contains",
  not_contains: "doesn't contain",
  matches: "matches the pattern",
  not_empty: "isn't empty",
  max_length: "is at most (characters)",
  min_length: "is at least (characters)",
  count: "has this many items",
  schema: "fits the JSON Schema",
  at_least: "is at least",
  at_most: "is at most",
  one_of: "is one of",
};

export type CheckKind =
  | "value"
  | "status"
  | "steps"
  | "tools"
  | "not_tools"
  | "trajectory"
  | "tool_calls"
  | "route"
  | "max_seconds"
  | "max_cost"
  | "max_tokens"
  | "judge"
  | "raw";

export const KIND_LABELS: Record<Exclude<CheckKind, "raw">, string> = {
  value: "A result value",
  status: "How the run ends",
  steps: "Steps that ran",
  tools: "Tools the agent called",
  not_tools: "Tools the agent didn't call",
  trajectory: "The order of tool calls",
  tool_calls: "How many tool calls (at most)",
  route: "The way a Decision went",
  max_seconds: "Time budget (seconds)",
  max_cost: "Cost budget (dollars)",
  max_tokens: "Token budget",
  judge: "AI judge with a rubric",
};

export interface CheckRow {
  kind: CheckKind;
  /** value: the path (output.answer); route: the Decision step; raw: the key. */
  path?: string;
  rule?: ValueRule;
  /** The value as typed (JSON for anything but plain text). */
  value?: string;
  /** steps, tools, not_tools, trajectory: names, comma-separated. */
  list?: string;
  /** trajectory: in_order, exact or any_order. */
  order?: "in_order" | "exact" | "any_order";
  /** judge */
  rubric?: string;
  passScore?: string;
}

const NUMBER_RULES = new Set<ValueRule>(["max_length", "min_length", "count", "at_least", "at_most"]);
const LIST_KINDS = new Set<CheckKind>(["steps", "tools", "not_tools"]);
const BUDGETS = new Set<CheckKind>(["max_seconds", "max_cost", "max_tokens"]);

/** How a value shows in a text box: text as it is, anything else as JSON. */
export function shown(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === undefined) return "";
  return JSON.stringify(value);
}

/** What was typed, as a value: JSON when it reads as JSON (numbers, true, lists), else text. */
export function typed(text: string): unknown {
  const trimmed = text.trim();
  if (!trimmed) return "";
  try {
    return JSON.parse(trimmed);
  } catch {
    return text;
  }
}

function names(list: unknown): string {
  return Array.isArray(list) ? list.join(", ") : shown(list);
}

function splitNames(text: string | undefined): string[] {
  return (text ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function toRows(expect: Record<string, unknown> | undefined): CheckRow[] {
  const rows: CheckRow[] = [];
  for (const [key, rule] of Object.entries(expect ?? {})) {
    if (key === "status") rows.push({ kind: "status", value: shown(rule) });
    else if (LIST_KINDS.has(key as CheckKind)) rows.push({ kind: key as CheckKind, list: names(rule) });
    else if (key === "trajectory") {
      const r = rule as { tools?: string[]; order?: CheckRow["order"] } | string[];
      if (Array.isArray(r)) rows.push({ kind: "trajectory", list: names(r), order: "in_order" });
      else rows.push({ kind: "trajectory", list: names(r?.tools ?? []), order: r?.order ?? "in_order" });
    } else if (key === "tool_calls" && rule && typeof rule === "object" && Object.keys(rule).join() === "max")
      rows.push({ kind: "tool_calls", value: shown((rule as { max: number }).max) });
    else if (key === "route" && rule && typeof rule === "object")
      for (const [step, exit] of Object.entries(rule)) rows.push({ kind: "route", path: step, value: shown(exit) });
    else if (BUDGETS.has(key as CheckKind)) rows.push({ kind: key as CheckKind, value: shown(rule) });
    else if (key === "judge") {
      const r = (typeof rule === "string" ? { rubric: rule } : rule) as { rubric?: string; pass_score?: number; model?: string; look_at?: string };
      if (r && (r.model || r.look_at)) rows.push({ kind: "raw", path: key, value: JSON.stringify(rule) });
      else rows.push({ kind: "judge", rubric: r?.rubric ?? "", passScore: r?.pass_score != null ? String(r.pass_score) : "" });
    } else if (key.startsWith("output") || key === "error" || key.includes(".")) {
      const rules = rule && typeof rule === "object" && !Array.isArray(rule) && Object.keys(rule).every((k) => (VALUE_RULES as readonly string[]).includes(k)) ? (rule as Record<string, unknown>) : { equals: rule };
      for (const [name, expected] of Object.entries(rules)) rows.push({ kind: "value", path: key, rule: name as ValueRule, value: name === "not_empty" ? "" : shown(expected) });
    } else rows.push({ kind: "raw", path: key, value: JSON.stringify(rule) });
  }
  return rows;
}

function ruleValue(rule: ValueRule, text: string): unknown {
  if (rule === "not_empty") return true;
  if (NUMBER_RULES.has(rule)) return Number(text);
  if (rule === "matches") return text;
  if (rule === "one_of" && !text.trim().startsWith("[")) return splitNames(text);
  return typed(text);
}

export function toExpect(rows: CheckRow[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const row of rows) {
    if (row.kind === "value" && row.path && row.rule) {
      const rules = (out[row.path] as Record<string, unknown> | undefined) ?? {};
      rules[row.rule] = ruleValue(row.rule, row.value ?? "");
      out[row.path] = rules;
    } else if (row.kind === "status") out.status = row.value || "ok";
    else if (LIST_KINDS.has(row.kind)) out[row.kind] = splitNames(row.list);
    else if (row.kind === "trajectory") {
      const tools = splitNames(row.list);
      out.trajectory = !row.order || row.order === "in_order" ? tools : { tools, order: row.order };
    } else if (row.kind === "tool_calls") out.tool_calls = { max: Number(row.value) };
    else if (row.kind === "route" && row.path) out.route = { ...((out.route as Record<string, unknown>) ?? {}), [row.path]: row.value ?? "" };
    else if (BUDGETS.has(row.kind)) out[row.kind] = Number(row.value);
    else if (row.kind === "judge") {
      const score = row.passScore?.trim();
      out.judge = score ? { rubric: row.rubric ?? "", pass_score: Number(score) } : { rubric: row.rubric ?? "" };
    } else if (row.kind === "raw" && row.path) out[row.path] = typed(row.value ?? "");
  }
  // A path with just "is exactly" reads better as `path: value`, as people write it by hand.
  for (const [key, value] of Object.entries(out)) {
    if (value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === 1 && "equals" in value) {
      const plain = (value as { equals: unknown }).equals;
      if (!(plain && typeof plain === "object")) out[key] = plain;
    }
  }
  return out;
}

/** A new row of a kind, with sensible values to start from. */
export function newRow(kind: CheckKind, outputs: string[] = []): CheckRow {
  if (kind === "value") return { kind, path: `output.${outputs[0] ?? "answer"}`, rule: "contains", value: "" };
  if (kind === "status") return { kind, value: "ok" };
  if (kind === "trajectory") return { kind, list: "", order: "in_order" };
  if (kind === "judge") return { kind, rubric: "", passScore: "0.7" };
  if (kind === "route") return { kind, path: "", value: "" };
  if (kind === "max_seconds") return { kind, value: "30" };
  if (kind === "max_cost") return { kind, value: "0.05" };
  if (kind === "max_tokens") return { kind, value: "5000" };
  if (kind === "tool_calls") return { kind, value: "5" };
  return { kind, list: "" };
}
