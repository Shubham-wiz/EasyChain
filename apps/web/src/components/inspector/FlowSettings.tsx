// The flow-wide panels: Flow Data fields with their update rules, how runs behave, and
// per-step run policies (retries, time limit, cache, joining parallel branches, breakpoints).

import { Plus, Trash2, Zap } from "lucide-react";
import { getStep, setDataFields, setFlowSettings, setRunPolicy, toIdent } from "../../lib/spec";
import type { DataField, FieldInfo, FieldType, UpdateRule } from "../../lib/types";
import { useCheck } from "../../state/check";
import { useFlow } from "../../state/flow";
import { useUi } from "../../state/ui";
import { CodeView } from "../CodeView";
import { Badge, Button, Field, Input, Select, Switch } from "../ui";

const TYPES: { value: FieldType; label: string }[] = [
  { value: "text", label: "Text" },
  { value: "number", label: "Number" },
  { value: "yes_no", label: "Yes/No" },
  { value: "list", label: "List" },
  { value: "object", label: "Object" },
  { value: "file", label: "File" },
  { value: "messages", label: "Messages" },
  { value: "any", label: "Anything" },
];

const RULES: { value: UpdateRule; label: string; help: string; pro?: boolean }[] = [
  { value: "replace", label: "Replace", help: "A new value replaces the old one." },
  { value: "append", label: "Add to the list", help: "New items are added to the end (lists and messages)." },
  { value: "add", label: "Add up", help: "Numbers are added together; lists and text are joined." },
  { value: "merge", label: "Merge", help: "New keys are added to the object." },
  { value: "custom", label: "Custom (Python)", help: "Your own combine(old, new) function.", pro: true },
];

const COMBINE_TEMPLATE = `def combine(old, new):
    """Return the field's new value from the old one and the update."""
    return new
`;

export function FlowDataEditor() {
  const spec = useFlow((s) => s.spec)!;
  const apply = useFlow((s) => s.apply);
  const analysis = useCheck((s) => s.analysis);
  const mode = useUi((s) => s.mode);
  const select = useUi((s) => s.select);
  const declared = spec.data ?? [];
  const inferred = (analysis?.fields ?? []).filter((f) => !f.private && !declared.some((d) => d.name === f.name));
  const info = (name: string): FieldInfo | undefined => analysis?.fields.find((f) => f.name === name);
  const stepName = (id: string) => spec.steps.find((s) => s.id === id)?.name || id;
  const write = (data: DataField[], key?: string) => apply((s) => setDataFields(s, data), key);
  const set = (i: number, patch: Partial<DataField>, key?: string) => write(declared.map((d, j) => (j === i ? { ...d, ...patch } : d)), key);

  const setBy = (name: string) => {
    const f = info(name);
    if (!f) return null;
    return f.written_by.length ? (
      f.written_by.map((id) => (
        <button key={id} type="button" className="mr-1 underline-offset-2 hover:underline" onClick={() => select([id])}>
          {stepName(id)}
        </button>
      ))
    ) : f.is_input ? (
      "Input"
    ) : (
      <span className="text-faint">nothing yet</span>
    );
  };

  return (
    <section aria-label="Flow Data">
      <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold">
        Flow Data
        {mode === "pro" && <span className="font-mono text-[10px] font-normal text-faint">state schema</span>}
      </h3>
      <p className="mb-2 text-xs text-muted">
        The named fields steps read and write. Use them as {"{field}"}. Declare a field to fix its type or choose how new values combine with old
        ones.
      </p>
      <div className="space-y-2">
        {declared.map((d, i) => (
          <div key={`${d.name}-${i}`} className="space-y-1.5 rounded-lg border border-border p-2" data-testid={`data-field-${d.name}`}>
            <div className="flex items-center gap-1.5">
              <Input
                aria-label="Field name"
                className="font-mono text-[12px]"
                defaultValue={d.name}
                onBlur={(e) => {
                  const clean = toIdent(e.target.value, "field");
                  e.target.value = clean;
                  if (clean !== d.name) set(i, { name: clean });
                }}
              />
              <Select aria-label="Field type" className="w-28" value={d.type} onChange={(e) => set(i, { type: e.target.value as FieldType })}>
                {TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </Select>
              <Button size="icon-sm" variant="ghost" aria-label={`Remove ${d.name}`} onClick={() => write(declared.filter((_, j) => j !== i))}>
                <Trash2 size={13} />
              </Button>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="shrink-0 text-xs text-muted">New values</span>
              <Select
                aria-label="Update rule"
                value={d.update}
                onChange={(e) => {
                  const update = e.target.value as UpdateRule;
                  set(i, update === "custom" && !d.combine ? { update, combine: COMBINE_TEMPLATE } : { update });
                }}
              >
                {RULES.filter((r) => mode === "pro" || !r.pro || r.value === d.update).map((r) => (
                  <option key={r.value} value={r.value}>
                    {r.label}
                  </option>
                ))}
              </Select>
            </div>
            <p className="text-[11px] text-faint">{RULES.find((r) => r.value === d.update)?.help}</p>
            {d.update === "custom" && (
              <CodeView value={d.combine || COMBINE_TEMPLATE} onChange={(code) => set(i, { combine: code }, `data:${d.name}:combine`)} height={130} label={`Update rule for ${d.name}`} />
            )}
            <Input aria-label="Description" placeholder="What it holds (optional)" value={d.description} onChange={(e) => set(i, { description: e.target.value }, `data:${d.name}:description`)} />
            <p className="text-[11px] text-muted">Set by: {setBy(d.name) ?? <span className="text-faint">nothing yet</span>}</p>
          </div>
        ))}
        {inferred.length > 0 && (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-faint">
                <th className="pb-1 font-medium">Field</th>
                <th className="pb-1 font-medium">Type</th>
                <th className="pb-1 font-medium">Set by</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {inferred.map((f) => (
                <tr key={f.name} className="border-t border-border align-top">
                  <td className="py-1.5 pr-2 font-mono">
                    {f.name}
                    {f.is_input && <Badge className="ml-1">in</Badge>}
                    {f.is_output && (
                      <Badge tone="accent" className="ml-1">
                        out
                      </Badge>
                    )}
                    {mode === "pro" && f.update !== "replace" && <span className="block text-[10px] text-faint">rule: {f.update}</span>}
                  </td>
                  <td className="py-1.5 pr-2 text-muted">{f.type}</td>
                  <td className="py-1.5 text-muted">{setBy(f.name)}</td>
                  <td className="py-1.5 text-right">
                    {f.name !== "messages" && (
                      <button
                        type="button"
                        className="text-[11px] text-accent underline"
                        onClick={() => write([...declared, { name: f.name, type: f.type, update: f.update, description: f.description }])}
                        aria-label={`Declare ${f.name}`}
                      >
                        Declare
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            const taken = new Set([...declared.map((d) => d.name), ...(analysis?.fields ?? []).map((f) => f.name)]);
            let n = 1;
            while (taken.has(`field_${n}`)) n += 1;
            write([...declared, { name: `field_${n}`, type: "text", update: "replace", description: "" }]);
          }}
        >
          <Plus size={13} /> Add a field
        </Button>
      </div>
    </section>
  );
}

export function FlowRunSettings() {
  const spec = useFlow((s) => s.spec)!;
  const apply = useFlow((s) => s.apply);
  const flowId = useFlow((s) => s.flowId);
  const setTriggersOpen = useUi((s) => s.setTriggersOpen);
  const settings = spec.settings ?? {};
  const chat = spec.steps.some((s) => s.type === "input" && s.settings.mode === "chat");
  const set = (patch: Record<string, unknown>, key: string) => apply((s) => setFlowSettings(s, patch), `settings:${key}`);
  const num = (v: string) => (v === "" ? null : Number(v));
  return (
    <section aria-label="How runs behave" className="space-y-3">
      <h3 className="text-sm font-semibold">How runs behave</h3>
      <Field label="Most rounds of steps" help="A run stops with an error after this many rounds (it guards against loops that never end)." htmlFor="flow-max-steps" technical="recursion_limit">
        <Input id="flow-max-steps" type="number" min={1} value={settings.max_steps ?? 25} onChange={(e) => set({ max_steps: num(e.target.value) ?? 25 }, "max_steps")} />
      </Field>
      <Field label="Most steps at the same time" help="Limits parallel branches and For Each items across the whole run. Empty: no limit." htmlFor="flow-max-parallel" technical="max_concurrency">
        <Input id="flow-max-parallel" type="number" min={1} placeholder="No limit" value={settings.max_parallel ?? ""} onChange={(e) => set({ max_parallel: num(e.target.value) }, "max_parallel")} />
      </Field>
      <Field label="Most runs at the same time" help="Runs beyond this wait in the queue. Empty: no limit." htmlFor="flow-max-runs">
        <Input id="flow-max-runs" type="number" min={1} placeholder="No limit" value={settings.max_concurrent_runs ?? ""} onChange={(e) => set({ max_concurrent_runs: num(e.target.value) }, "max_concurrent_runs")} />
      </Field>
      {chat && (
        <Field label="When a new message arrives while busy" help="What happens if someone sends another message before the flow has answered." htmlFor="flow-double-texting">
          <Select id="flow-double-texting" value={settings.double_texting ?? "queue"} onChange={(e) => set({ double_texting: e.target.value === "queue" ? null : e.target.value }, "double_texting")}>
            <option value="queue">Answer it after the current one</option>
            <option value="reject">Refuse it until the flow is done</option>
            <option value="interrupt">Stop the current answer and take the new message</option>
            <option value="rollback">Undo the current answer and take the new message</option>
          </Select>
        </Field>
      )}
      {flowId && (
        <Button variant="outline" size="sm" onClick={() => setTriggersOpen(true)} data-testid="open-triggers">
          <Zap size={13} /> Triggers: webhook, schedule, upload…
        </Button>
      )}
    </section>
  );
}

/** Retries, time limit, cache, waiting for parallel branches and breakpoints for one step. */
export function RunPolicySection({ stepId }: { stepId: string }) {
  const step = useFlow((s) => (s.spec ? getStep(s.spec, stepId) : undefined));
  const incoming = useFlow((s) => s.spec?.connections.filter((c) => c.to === stepId).length ?? 0);
  const apply = useFlow((s) => s.apply);
  const before = useUi((s) => s.breakpoints.before.includes(stepId));
  const after = useUi((s) => s.breakpoints.after.includes(stepId));
  const toggle = useUi((s) => s.toggleBreakpoint);
  if (!step || step.type === "input" || step.type === "output") return null;
  const policy = step.run ?? {};
  const set = (patch: Record<string, unknown>, key: string) => apply((s) => setRunPolicy(s, stepId, patch), `${stepId}:run:${key}`);
  const num = (v: string) => (v === "" ? null : Number(v));
  return (
    <div className="space-y-3 border-t border-border pt-3" data-testid="run-policy">
      <p className="text-xs font-semibold text-muted">When it runs</p>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Retries" help="Try again this many times if the step fails." htmlFor={`${stepId}-retries`} technical="RetryPolicy">
          <Input id={`${stepId}-retries`} type="number" min={0} max={10} value={policy.retries ?? 0} onChange={(e) => set({ retries: num(e.target.value) }, "retries")} />
        </Field>
        <Field label="Time limit (s)" help="Stop the step after this many seconds." htmlFor={`${stepId}-timeout`} technical="timeout">
          <Input id={`${stepId}-timeout`} type="number" min={1} placeholder="None" value={policy.timeout ?? ""} onChange={(e) => set({ timeout: num(e.target.value) }, "timeout")} />
        </Field>
      </div>
      {!!policy.retries && (
        <Field label="Wait before retrying (s)" help="Doubles after each try." htmlFor={`${stepId}-retry-wait`}>
          <Input id={`${stepId}-retry-wait`} type="number" min={0.1} step={0.5} value={policy.retry_wait ?? 1} onChange={(e) => set({ retry_wait: num(e.target.value) === 1 ? null : num(e.target.value) }, "retry_wait")} />
        </Field>
      )}
      <label className="flex items-center gap-2 text-xs">
        <Switch checked={!!policy.cache} onCheckedChange={(v) => set({ cache: v }, "cache")} label="Reuse results" />
        Reuse the result when the step gets the same inputs again
      </label>
      {policy.cache && (
        <Field label="Keep cached results for (s)" htmlFor={`${stepId}-cache-ttl`} technical="CachePolicy(ttl)">
          <Input id={`${stepId}-cache-ttl`} type="number" min={1} placeholder="Forever" value={policy.cache_ttl ?? ""} onChange={(e) => set({ cache_ttl: num(e.target.value) }, "cache_ttl")} />
        </Field>
      )}
      {(incoming > 1 || policy.wait_for_all) && (
        <label className="flex items-center gap-2 text-xs">
          <Switch checked={!!policy.wait_for_all} onCheckedChange={(v) => set({ wait_for_all: v }, "wait_for_all")} label="Wait for all branches" />
          Wait until every branch leading here has finished
        </label>
      )}
      <div className="space-y-1.5 rounded-lg bg-surface-2/60 p-2">
        <p className="text-[11px] text-muted">Pause test runs here to look at the Flow Data (not saved with the flow).</p>
        <label className="flex items-center gap-2 text-xs">
          <Switch checked={before} onCheckedChange={() => toggle(stepId, "before")} label="Pause before this step" />
          Pause before this step
        </label>
        <label className="flex items-center gap-2 text-xs">
          <Switch checked={after} onCheckedChange={() => toggle(stepId, "after")} label="Pause after this step" />
          Pause after this step
        </label>
      </div>
    </div>
  );
}
