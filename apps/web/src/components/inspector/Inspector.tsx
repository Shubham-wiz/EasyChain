import { AlertTriangle, ChevronDown, ChevronRight, CircleAlert, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { applyFix } from "../../lib/fixes";
import { getStep, renameStepId, toIdent, updateSettings, updateStep } from "../../lib/spec";
import type { Issue } from "../../lib/types";
import { cn, preview } from "../../lib/utils";
import { useStepInfo } from "../../state/catalog";
import { useCheck } from "../../state/check";
import { useFlow } from "../../state/flow";
import { useRun } from "../../state/run";
import { useUi } from "../../state/ui";
import { colorsFor, iconFor } from "../canvas/stepMeta";
import { CodeView } from "../CodeView";
import { Button, Field, Input, Tabs, TabsContent, TabsList, TabsTrigger, Textarea } from "../ui";
import { isVisible, renderControl } from "./fields";
import { FlowDataEditor, FlowRunSettings, RunPolicySection } from "./FlowSettings";

export function IssueList({ issues, compact, retry }: { issues: Issue[]; compact?: boolean; retry?: () => void }) {
  const steps = useFlow((s) => s.spec?.steps);
  const focus = useUi((s) => s.focus);
  if (!issues.length) return null;
  return (
    <ul className="space-y-1.5" aria-label="Problems">
      {issues.map((issue, i) => {
        const step = issue.step ? steps?.find((s) => s.id === issue.step) : undefined;
        return (
          <li
            key={`${issue.code}-${issue.step}-${i}`}
            className={cn(
              "rounded-lg border px-2.5 py-2 text-xs leading-relaxed",
              issue.level === "error" ? "border-danger/30 bg-danger-soft" : "border-warn/30 bg-warn-soft",
            )}
          >
            <div className="flex gap-2">
              {issue.level === "error" ? (
                <CircleAlert size={14} className="mt-0.5 shrink-0 text-danger" />
              ) : (
                <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warn" />
              )}
              <div className="min-w-0 flex-1 text-text">
                {!compact && step && (
                  <button type="button" className="mr-1 font-semibold underline-offset-2 hover:underline" onClick={() => focus(step.id, issue.setting ?? "")}>
                    {step.name || step.id}:
                  </button>
                )}
                {issue.message}
                {issue.hint && <span className="block text-muted">{issue.hint}</span>}
                {issue.fix && (
                  <Button size="sm" variant="outline" className="mt-1.5" onClick={() => applyFix(issue.fix!, issue.step, retry)}>
                    <Wrench size={12} /> {issue.fix.label}
                  </Button>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function FlowPanel() {
  const spec = useFlow((s) => s.spec)!;
  const apply = useFlow((s) => s.apply);
  const issues = useCheck((s) => s.issues);
  const flowIssues = issues.filter((i) => !i.step);
  return (
    <div className="space-y-5 p-4">
      <div className="space-y-3">
        <Field label="Flow name" htmlFor="flow-name">
          <Input id="flow-name" value={spec.name} onChange={(e) => apply((s) => ({ ...s, name: e.target.value || "Untitled flow" }), "flow:name")} />
        </Field>
        <Field label="What it does" htmlFor="flow-description">
          <Textarea
            id="flow-description"
            rows={2}
            value={spec.description}
            placeholder="One or two sentences, for your team and the exported code"
            onChange={(e) => apply((s) => ({ ...s, description: e.target.value }), "flow:description")}
          />
        </Field>
      </div>
      {flowIssues.length > 0 && <IssueList issues={flowIssues} />}
      <FlowDataEditor />
      <FlowRunSettings />
    </div>
  );
}

function StepRunResult({ stepId }: { stepId: string }) {
  const run = useRun((s) => s.steps[stepId]);
  if (!run || run.status === "running") return null;
  if (run.status === "error") return null; // shown by the error card in the run panel and on the node
  const entries = Object.entries(run.output ?? {});
  if (!entries.length) return null;
  return (
    <div className="rounded-lg border border-ok/30 bg-ok-soft px-2.5 py-2 text-xs">
      <p className="mb-1 font-semibold text-ok">Last run{run.exit ? ` · took “${run.exit}”` : ""}</p>
      {entries.map(([k, v]) => (
        <p key={k} className="text-text">
          <span className="font-mono text-muted">{k}</span>: <span className="whitespace-pre-wrap">{preview(v, 300)}</span>
        </p>
      ))}
    </div>
  );
}

function StepPanel({ stepId }: { stepId: string }) {
  const step = useFlow((s) => s.spec?.steps.find((x) => x.id === stepId));
  const apply = useFlow((s) => s.apply);
  const info = useStepInfo(step?.type);
  const mode = useUi((s) => s.mode);
  const focusSetting = useUi((s) => s.focusSetting);
  const analysis = useCheck((s) => s.analysis);
  const allIssues = useCheck((s) => s.issues);
  const compiled = useCheck((s) => s.compiled);
  const [moreOpen, setMoreOpen] = useState(mode === "pro");
  const [tab, setTab] = useState("settings");
  const formRef = useRef<HTMLDivElement>(null);
  const issues = useMemo(() => allIssues.filter((i) => i.step === stepId), [allIssues, stepId]);

  useEffect(() => setMoreOpen(mode === "pro"), [mode, stepId]);

  useEffect(() => {
    if (!focusSetting || focusSetting.step !== stepId || !focusSetting.key) return;
    setTab("settings");
    const advanced = info?.form.find((f) => f.key === focusSetting.key)?.advanced;
    if (advanced) setMoreOpen(true);
    requestAnimationFrame(() => {
      const el = formRef.current?.querySelector<HTMLElement>(`[data-setting="${focusSetting.key}"]`);
      el?.scrollIntoView({ block: "center", behavior: "smooth" });
      el?.querySelector<HTMLElement>("input, textarea, select")?.focus();
    });
  }, [focusSetting, stepId, info]);

  if (!step || !info) return null;
  const Icon = iconFor(info.icon);
  const fields = analysis?.fields ?? [];
  const upstream = analysis?.upstream[stepId];
  const visible = info.form.filter((f) => (mode === "pro" || !f.pro) && isVisible(f, step.settings));
  const basic = visible.filter((f) => !f.advanced);
  const advanced = visible.filter((f) => f.advanced);
  const issueFor = (key: string) => {
    const issue = issues.find((i) => i.setting === key);
    return issue ? { level: issue.level, message: issue.message } : null;
  };

  const control = (f: (typeof visible)[number]) => (
    <div key={f.key} data-setting={f.key}>
      <Field label={f.label} help={f.help} example={f.example} technical={f.technical} pro={mode === "pro"} htmlFor={`${stepId}-${f.key}`} issue={issueFor(f.key)}>
        {renderControl({
          field: f,
          value: step.settings[f.key],
          settings: step.settings,
          fields,
          upstream,
          id: `${stepId}-${f.key}`,
          onChange: (value) => apply((s) => updateSettings(s, stepId, { [f.key]: value }), `${stepId}:${f.key}`),
        })}
      </Field>
    </div>
  );

  return (
    <div className="flex h-full flex-col">
      <div className="space-y-3 border-b border-border p-4">
        <div className="flex items-center gap-2.5">
          <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", colorsFor(info.category).chip)}>
            <Icon size={16} />
          </div>
          <div className="min-w-0 flex-1">
            <input
              aria-label="Step name"
              className="w-full rounded bg-transparent text-[15px] font-semibold outline-none hover:bg-surface-2 focus:bg-surface-2"
              value={step.name}
              onChange={(e) => apply((s) => updateStep(s, stepId, { name: e.target.value }), `${stepId}:name`)}
            />
            <p className="text-xs text-muted">
              {info.label}
              {mode === "pro" && <span className="font-mono text-faint"> · {info.technical}</span>}
            </p>
          </div>
        </div>
        <p className="text-xs leading-relaxed text-muted">{info.summary}</p>
        <IssueList issues={issues} compact />
        <StepRunResult stepId={stepId} />
      </div>
      <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
        <TabsList>
          <TabsTrigger value="settings">Settings</TabsTrigger>
          <TabsTrigger value="code">Code</TabsTrigger>
        </TabsList>
        <TabsContent value="settings" className="scroll-thin min-h-0 flex-1 overflow-y-auto">
          <div ref={formRef} className="space-y-4 p-4">
            {basic.map(control)}
            {(advanced.length > 0 || mode === "pro" || (step.type !== "input" && step.type !== "output")) && (
              <div className="border-t border-border pt-3">
                <button
                  type="button"
                  className="flex items-center gap-1 text-xs font-medium text-muted hover:text-text"
                  aria-expanded={moreOpen}
                  onClick={() => setMoreOpen(!moreOpen)}
                >
                  {moreOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />} More options
                </button>
                {moreOpen && (
                  <div className="mt-3 space-y-4">
                    {advanced.map(control)}
                    <RunPolicySection stepId={stepId} />
                    {mode === "pro" && <StepIdField stepId={stepId} />}
                  </div>
                )}
              </div>
            )}
          </div>
        </TabsContent>
        <TabsContent value="code" className="min-h-0 flex-1 overflow-y-auto p-4">
          <p className="mb-2 text-xs text-muted">The LangGraph code this step compiles to. It updates as you edit.</p>
          {compiled?.snippets[stepId] ? (
            <CodeView value={compiled.snippets[stepId]} readOnly height={360} label={`Code for ${step.name}`} />
          ) : step.type === "input" || step.type === "output" ? (
            <p className="text-xs text-faint">
              {step.type === "input" ? "Input becomes the graph's START and its input schema." : "Output becomes END and the output schema."} See
              the whole file with Export.
            </p>
          ) : (
            <p className="text-xs text-faint">Fix the problems above to see this step's code.</p>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

function StepIdField({ stepId }: { stepId: string }) {
  const apply = useFlow((s) => s.apply);
  const select = useUi((s) => s.select);
  const [draft, setDraft] = useState(stepId);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setDraft(stepId), [stepId]);
  return (
    <Field label="Step id" help="Used as the LangGraph node name and function name in exported code." technical="node name" pro>
      <Input
        className="font-mono text-[13px]"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          const clean = toIdent(draft);
          const spec = useFlow.getState().spec!;
          if (clean === stepId) return setDraft(stepId);
          if (getStep(spec, clean)) {
            setError(`Another step is already called ${clean}.`);
            return setDraft(stepId);
          }
          setError(null);
          apply((s) => renameStepId(s, stepId, clean));
          select([clean]);
        }}
      />
      {error && <p className="text-xs text-danger">{error}</p>}
    </Field>
  );
}

export function Inspector() {
  const selected = useUi((s) => s.selected);
  const exists = useFlow((s) => (selected.length === 1 ? !!s.spec?.steps.some((x) => x.id === selected[0]) : false));
  if (selected.length > 1) {
    return <p className="p-4 text-sm text-muted">{selected.length} steps selected. Press Delete to remove them, or {navigator.platform.includes("Mac") ? "⌘" : "Ctrl"}+C to copy.</p>;
  }
  return exists ? <StepPanel key={selected[0]} stepId={selected[0]} /> : <FlowPanel />;
}
