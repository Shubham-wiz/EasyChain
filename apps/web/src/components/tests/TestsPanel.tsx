import { CheckCircle2, ChevronDown, ChevronRight, CircleAlert, CircleMinus, FlaskConical, Loader2, Play, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import { type CheckKind, type CheckRow, KIND_LABELS, newRow, RULE_LABELS, shown, toExpect, toRows, typed, VALUE_RULES, type ValueRule } from "../../lib/checks";
import type { TestCase } from "../../lib/copilot";
import type { CaseRunResult, Step, TestSetInfo } from "../../lib/types";
import { useCan, useRoleNote } from "../../state/auth";
import { useFlow } from "../../state/flow";
import { Badge, Button, Input, Locked, Select, Switch, Textarea } from "../ui";

interface InputField {
  name: string;
  type: string;
}

function message(err: unknown): { text: string; problems: string[] } {
  if (err instanceof ApiError) return { text: err.message, problems: err.problems };
  return { text: err instanceof Error ? err.message : String(err), problems: [] };
}

function useFlowShape() {
  const spec = useFlow((s) => s.spec);
  const input = spec?.steps.find((s) => s.type === "input") as Step | undefined;
  const output = spec?.steps.find((s) => s.type === "output") as Step | undefined;
  const fields = ((input?.settings.fields ?? []) as InputField[]).map((f) => ({ name: f.name, type: f.type ?? "text" }));
  const chat = input?.settings.mode === "chat";
  const outputs = ((output?.settings.fields ?? []) as string[]).filter((f) => typeof f === "string");
  return { fields, chat, outputs };
}

function Verdict({ result }: { result?: CaseRunResult }) {
  if (!result) return null;
  if (!result.passed) return <Badge tone="danger">Failed</Badge>;
  return result.skipped.length ? <Badge tone="warn">Passed, some skipped</Badge> : <Badge tone="ok">Passed</Badge>;
}

function ResultView({ result }: { result: CaseRunResult }) {
  const { seconds, tokens, cost, tools } = result.metrics;
  return (
    <div className="space-y-1.5 rounded-lg border border-border bg-surface-2 p-2 text-xs" data-testid={`result-${result.name}`}>
      <ul className="space-y-1" aria-label={`Checks of ${result.name}`}>
        {result.checks.map((c, i) => (
          <li key={i} className="flex gap-1.5">
            {c.passed === true ? (
              <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-ok" aria-label="passed" />
            ) : c.passed === false ? (
              <CircleAlert size={13} className="mt-0.5 shrink-0 text-danger" aria-label="failed" />
            ) : (
              <CircleMinus size={13} className="mt-0.5 shrink-0 text-warn" aria-label="skipped" />
            )}
            <span className="min-w-0 break-words">
              <span className="font-mono">{c.name}</span>
              {c.message && <span className="text-muted"> {c.message}</span>}
            </span>
          </li>
        ))}
      </ul>
      <p className="text-muted">
        {seconds.toFixed(1)} s · {tokens} tokens · {cost == null ? "cost unknown" : `$${cost.toFixed(4)}`}
        {tools.length > 0 && ` · tools: ${tools.join(" → ")}`}
      </p>
      {result.output !== undefined && result.output !== null && (
        <details>
          <summary className="cursor-pointer text-muted">Output</summary>
          <pre className="scroll-thin mt-1 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px]">{JSON.stringify(result.output, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

/** A JSON text box that only changes the value once what's typed reads as JSON. */
function JsonBox({ label, value, onChange, placeholder, rows = 3 }: { label: string; value: unknown; onChange: (v: unknown) => void; placeholder?: string; rows?: number }) {
  const [text, setText] = useState(value === undefined ? "" : JSON.stringify(value, null, 2));
  const [bad, setBad] = useState(false);
  return (
    <div className="space-y-1">
      <Textarea
        aria-label={label}
        aria-invalid={bad}
        rows={rows}
        className="font-mono text-xs"
        placeholder={placeholder}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          if (!text.trim()) {
            setBad(false);
            onChange(undefined);
            return;
          }
          try {
            onChange(JSON.parse(text));
            setBad(false);
          } catch {
            setBad(true);
          }
        }}
      />
      {bad && <p className="text-[11px] text-danger">That isn't valid JSON yet, so it isn't kept.</p>}
    </div>
  );
}

function CheckEditor({ row, onChange, onRemove, outputs }: { row: CheckRow; onChange: (r: CheckRow) => void; onRemove: () => void; outputs: string[] }) {
  const label = row.kind === "raw" ? row.path ?? "check" : KIND_LABELS[row.kind];
  const set = (patch: Partial<CheckRow>) => onChange({ ...row, ...patch });
  return (
    <li className="space-y-1.5 rounded-md border border-border p-2" data-testid="check-row">
      <div className="flex items-center gap-1.5">
        <span className="min-w-0 flex-1 truncate text-xs font-medium">{label}</span>
        <Button size="icon-sm" variant="ghost" aria-label={`Remove the check: ${label}`} onClick={onRemove}>
          <Trash2 size={12} />
        </Button>
      </div>
      {row.kind === "value" && (
        <div className="grid grid-cols-2 gap-1.5">
          <Input aria-label="Which value" className="h-8 font-mono text-xs" list="test-outputs" value={row.path ?? ""} onChange={(e) => set({ path: e.target.value })} />
          <Select aria-label="Rule" className="h-8 text-xs" value={row.rule} onChange={(e) => set({ rule: e.target.value as ValueRule })}>
            {VALUE_RULES.map((r) => (
              <option key={r} value={r}>
                {RULE_LABELS[r]}
              </option>
            ))}
          </Select>
          {row.rule !== "not_empty" &&
            (row.rule === "schema" ? (
              <Textarea aria-label="Expected" rows={3} className="col-span-2 font-mono text-xs" placeholder='{"type": "object", "required": ["id"]}' value={row.value ?? ""} onChange={(e) => set({ value: e.target.value })} />
            ) : (
              <Input aria-label="Expected" className="col-span-2 h-8 text-xs" value={row.value ?? ""} onChange={(e) => set({ value: e.target.value })} />
            ))}
          <datalist id="test-outputs">
            {outputs.map((o) => (
              <option key={o} value={`output.${o}`} />
            ))}
          </datalist>
        </div>
      )}
      {row.kind === "status" && (
        <Select aria-label="How the run ends" className="h-8 text-xs" value={row.value} onChange={(e) => set({ value: e.target.value })}>
          <option value="ok">It finishes</option>
          <option value="error">It fails</option>
          <option value="paused">It waits for someone</option>
        </Select>
      )}
      {(row.kind === "steps" || row.kind === "tools" || row.kind === "not_tools" || row.kind === "trajectory") && (
        <Input aria-label={label} className="h-8 font-mono text-xs" placeholder="names, separated by commas" value={row.list ?? ""} onChange={(e) => set({ list: e.target.value })} />
      )}
      {row.kind === "trajectory" && (
        <Select aria-label="Order" className="h-8 text-xs" value={row.order ?? "in_order"} onChange={(e) => set({ order: e.target.value as CheckRow["order"] })}>
          <option value="in_order">In this order (others may come between)</option>
          <option value="exact">Exactly these, in this order</option>
          <option value="any_order">All of them, in any order</option>
        </Select>
      )}
      {row.kind === "route" && (
        <div className="grid grid-cols-2 gap-1.5">
          <Input aria-label="Decision step" className="h-8 font-mono text-xs" placeholder="step id" value={row.path ?? ""} onChange={(e) => set({ path: e.target.value })} />
          <Input aria-label="Exit it takes" className="h-8 text-xs" placeholder="exit" value={row.value ?? ""} onChange={(e) => set({ value: e.target.value })} />
        </div>
      )}
      {(row.kind === "tool_calls" || row.kind === "max_seconds" || row.kind === "max_cost" || row.kind === "max_tokens") && (
        <Input aria-label={label} type="number" min={0} step="any" className="h-8 text-xs" value={row.value ?? ""} onChange={(e) => set({ value: e.target.value })} />
      )}
      {row.kind === "judge" && (
        <>
          <Textarea aria-label="Rubric" rows={3} className="text-xs" placeholder="What a good answer does: polite, says when the refund arrives, promises nothing else." value={row.rubric ?? ""} onChange={(e) => set({ rubric: e.target.value })} />
          <label className="flex items-center gap-2 text-xs text-muted">
            Passes from
            <Input aria-label="Pass score" type="number" min={0} max={1} step={0.05} className="h-8 w-20 text-xs" value={row.passScore ?? ""} onChange={(e) => set({ passScore: e.target.value })} />
            (0 to 1). Needs a model key; skipped without one.
          </label>
        </>
      )}
      {row.kind === "raw" && <Textarea aria-label={label} rows={2} className="font-mono text-xs" value={row.value ?? ""} onChange={(e) => set({ value: e.target.value })} />}
    </li>
  );
}

function CaseEditor({ value, onChange }: { value: TestCase; onChange: (c: TestCase) => void }) {
  const { fields, chat, outputs } = useFlowShape();
  const [rows, setRows] = useState<CheckRow[]>(() => toRows(value.expect as Record<string, unknown> | undefined));
  const inputs = (value.inputs ?? {}) as Record<string, unknown>;
  const answers = (value.answers ?? []) as { action: string; value?: unknown; comment?: string }[];
  const setRowsAndCase = (next: CheckRow[]) => {
    setRows(next);
    const expect = toExpect(next);
    const { expect: _old, ...rest } = value;
    onChange(Object.keys(expect).length ? { ...rest, expect } : rest);
  };
  const setInput = (name: string, text: string, type: string) => onChange({ ...value, inputs: { ...inputs, [name]: type === "text" ? text : typed(text) } });
  const message = chat ? ((inputs.messages as { content?: string }[] | undefined)?.at(-1)?.content ?? "") : "";
  return (
    <div className="space-y-3">
      <Input aria-label="Case name" className="h-8 text-sm font-medium" value={value.name} onChange={(e) => onChange({ ...value, name: e.target.value })} />
      <section className="space-y-1.5" aria-label="Inputs">
        <p className="text-xs font-semibold">Inputs</p>
        {value.turns ? (
          <JsonBox label="Turns" value={value.turns} onChange={(v) => onChange({ ...value, turns: (v as unknown[]) ?? [] })} rows={5} />
        ) : chat ? (
          <Textarea aria-label="Message" rows={2} className="text-xs" value={message} onChange={(e) => onChange({ ...value, inputs: { ...inputs, messages: [{ role: "user", content: e.target.value }] } })} />
        ) : (
          fields.map((f) => (
            <label key={f.name} className="block space-y-0.5 text-xs">
              <span className="font-mono text-muted">{f.name}</span>
              <Input aria-label={`Input ${f.name}`} className="h-8 text-xs" value={shown(inputs[f.name])} onChange={(e) => setInput(f.name, e.target.value, f.type)} />
            </label>
          ))
        )}
      </section>
      <section className="space-y-1.5" aria-label="Ask a Human answers">
        <p className="text-xs font-semibold">Answers to Ask a Human, in order</p>
        {answers.map((a, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <Select aria-label={`Answer ${i + 1}`} className="h-8 w-28 text-xs" value={a.action} onChange={(e) => onChange({ ...value, answers: answers.map((x, j) => (j === i ? { ...x, action: e.target.value } : x)) })}>
              <option value="approve">Approve</option>
              <option value="reject">Reject</option>
            </Select>
            <Input aria-label={`Comment ${i + 1}`} className="h-8 text-xs" placeholder="comment (optional)" value={a.comment ?? ""} onChange={(e) => onChange({ ...value, answers: answers.map((x, j) => (j === i ? { ...x, comment: e.target.value || undefined } : x)) })} />
            <Button size="icon-sm" variant="ghost" aria-label={`Remove answer ${i + 1}`} onClick={() => onChange({ ...value, answers: answers.filter((_, j) => j !== i) })}>
              <Trash2 size={12} />
            </Button>
          </div>
        ))}
        <Button size="sm" variant="ghost" onClick={() => onChange({ ...value, answers: [...answers, { action: "approve" }] })}>
          <Plus size={12} /> Add an answer
        </Button>
      </section>
      <section className="space-y-1.5" aria-label="Scripted AI turns">
        <p className="text-xs font-semibold">What the stand-in AI says (optional)</p>
        <JsonBox
          label="Scripted AI turns"
          value={value.script}
          onChange={(v) => {
            const { script: _old, ...rest } = value;
            onChange(v === undefined ? rest : { ...rest, script: v as unknown[] });
          }}
          placeholder='[{"call": "find_order", "args": {"id": 12}}, {"answer": {"reply": "It ships today."}}]'
        />
        <p className="text-[11px] text-faint">Used only when the case runs with the stand-in AI; real models ignore it.</p>
      </section>
      <section className="space-y-1.5" aria-label="Checks">
        <p className="text-xs font-semibold">Checks</p>
        <ul className="space-y-1.5">
          {rows.map((r, i) => (
            <CheckEditor key={i} row={r} outputs={outputs} onChange={(next) => setRowsAndCase(rows.map((x, j) => (j === i ? next : x)))} onRemove={() => setRowsAndCase(rows.filter((_, j) => j !== i))} />
          ))}
        </ul>
        <Select
          aria-label="Add a check"
          className="h-8 text-xs"
          value=""
          onChange={(e) => {
            if (e.target.value) setRowsAndCase([...rows, newRow(e.target.value as CheckKind, outputs)]);
          }}
        >
          <option value="">Add a check…</option>
          {Object.entries(KIND_LABELS).map(([kind, text]) => (
            <option key={kind} value={kind}>
              {text}
            </option>
          ))}
        </Select>
      </section>
    </div>
  );
}

/** The flow's Test Set: edit cases, run them, see what passed (Phase 5.1, docs/testing.md). */
export function TestsPanel() {
  const flowId = useFlow((s) => s.flowId);
  const spec = useFlow((s) => s.spec);
  const canEdit = useCan("editor");
  const canRun = useCan("runner");
  const cantEdit = useRoleNote("editor", "change test cases");
  const [info, setInfo] = useState<TestSetInfo | null>(null);
  const [cases, setCases] = useState<TestCase[]>([]);
  const [standIn, setStandIn] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [results, setResults] = useState<Record<string, CaseRunResult>>({});
  const [summary, setSummary] = useState<string | null>(null);
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<{ text: string; problems: string[] } | null>(null);

  useEffect(() => {
    if (!flowId) return;
    setResults({});
    setSummary(null);
    api
      .testSet(flowId)
      .then((t) => {
        setInfo(t);
        setCases(t.cases);
        setStandIn(t.stand_in);
        setDirty(false);
        setError(null);
      })
      .catch((err) => setError(message(err)));
  }, [flowId]);

  if (!flowId) return null;
  const change = (next: TestCase[]) => {
    setCases(next);
    setDirty(true);
  };
  const save = async (): Promise<boolean> => {
    setBusy("save");
    try {
      await api.saveTestSet(flowId, { cases, stand_in: standIn });
      setDirty(false);
      setError(null);
      return true;
    } catch (err) {
      setError(message(err));
      return false;
    } finally {
      setBusy(null);
    }
  };
  const run = async (only?: string) => {
    if (dirty && canEdit && !(await save())) return;
    setBusy(only ?? "all");
    setError(null);
    try {
      const ran = await api.runTestSet(flowId, { cases: only ? [only] : undefined, variables, spec: canEdit && spec ? spec : undefined });
      setResults((prev) => ({ ...(only ? prev : {}), ...Object.fromEntries(ran.results.map((r) => [r.name, r])) }));
      setSummary(`${ran.passed} of ${ran.total} passed${ran.stand_in ? ", with the stand-in AI" : ""}.`);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(null);
    }
  };
  const names = new Set(cases.map((c) => c.name));
  const fresh = () => {
    let n = cases.length + 1;
    while (names.has(`Case ${n}`)) n += 1;
    return `Case ${n}`;
  };
  const variablesNeeded = info?.variables ?? [];

  return (
    <div className="scroll-thin h-full space-y-3 overflow-y-auto p-3" data-testid="tests-panel">
      <div className="flex items-center gap-2">
        <FlaskConical size={15} className="text-accent" />
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">Test Set</h2>
        {canRun && (
          <Button size="sm" variant="primary" disabled={busy !== null || cases.length === 0} onClick={() => void run()}>
            {busy === "all" ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Run all
          </Button>
        )}
      </div>
      <p className="text-xs text-muted">
        Cases with inputs and what to expect, kept next to the flow in <span className="font-mono">{info?.file ?? "…"}</span>. Run them after each change.
      </p>
      {!canEdit && (
        <p className="rounded-lg border border-border bg-surface-2 px-3 py-2 text-xs text-muted" role="note">
          {cantEdit}
        </p>
      )}
      <label className="flex items-center gap-2 text-xs">
        <Switch checked={standIn} label="Run with the stand-in AI" onCheckedChange={(v) => canEdit && (setStandIn(v), setDirty(true))} />
        Run with the stand-in AI (free, offline; scripted turns apply)
      </label>
      {variablesNeeded.length > 0 && (
        <div className="space-y-1">
          {variablesNeeded.map((v) => (
            <label key={v} className="flex items-center gap-2 text-xs">
              <span className="w-24 shrink-0 font-mono">${"{" + v + "}"}</span>
              <Input aria-label={`Value of ${v}`} className="h-8 text-xs" value={variables[v] ?? ""} onChange={(e) => setVariables({ ...variables, [v]: e.target.value })} />
            </label>
          ))}
        </div>
      )}
      {summary && (
        <p className="text-xs font-medium" role="status">
          {summary}
        </p>
      )}
      {error && (
        <div className="rounded-lg border border-danger/30 bg-danger-soft p-2 text-xs" role="alert">
          <p>{error.text}</p>
          {error.problems.length > 0 && (
            <ul className="mt-1 list-disc pl-4">
              {error.problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {cases.length === 0 && <p className="text-xs text-muted">No cases yet.{canEdit ? " Add one, or ask the copilot for some." : ""}</p>}
      <ul className="space-y-2" aria-label="Cases">
        {cases.map((c, i) => (
          <li key={i} className="rounded-lg border border-border" data-testid={`case-${c.name}`}>
            <div className="flex items-center gap-1.5 p-2">
              <button type="button" className="flex min-w-0 flex-1 items-center gap-1 text-left text-sm" aria-expanded={open === i} onClick={() => setOpen(open === i ? null : i)}>
                {open === i ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <span className="truncate">{c.name}</span>
              </button>
              <Verdict result={results[c.name]} />
              {canRun && (
                <Button size="icon-sm" variant="ghost" aria-label={`Run ${c.name}`} disabled={busy !== null} onClick={() => void run(c.name)}>
                  {busy === c.name ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                </Button>
              )}
              {canEdit && (
                <Button
                  size="icon-sm"
                  variant="ghost"
                  aria-label={`Remove ${c.name}`}
                  onClick={() => {
                    change(cases.filter((_, j) => j !== i));
                    setOpen(null);
                  }}
                >
                  <Trash2 size={12} />
                </Button>
              )}
            </div>
            {results[c.name] && !results[c.name].passed && open !== i && (
              <ul className="px-3 pb-2 text-xs text-danger">
                {results[c.name].failures.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            )}
            {open === i && (
              <div className="space-y-3 border-t border-border p-2">
                <Locked locked={!canEdit}>
                  <CaseEditor key={`${flowId}:${i}`} value={c} onChange={(next) => change(cases.map((x, j) => (j === i ? next : x)))} />
                </Locked>
                {results[c.name] && <ResultView result={results[c.name]} />}
              </div>
            )}
          </li>
        ))}
      </ul>
      {canEdit && (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              change([...cases, { name: fresh(), inputs: {}, expect: { status: "ok" } }]);
              setOpen(cases.length);
            }}
          >
            <Plus size={13} /> Add a case
          </Button>
          <Button size="sm" variant={dirty ? "primary" : "ghost"} disabled={!dirty || busy !== null} onClick={() => void save()}>
            {busy === "save" ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />} {dirty ? "Save the Test Set" : "Saved"}
          </Button>
        </div>
      )}
    </div>
  );
}
