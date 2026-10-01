import { Handle, Position, type NodeProps } from "@xyflow/react";
import { AlertTriangle, CheckCircle2, CircleAlert, ExternalLink, Hand, Loader2 } from "lucide-react";
import { memo, useMemo } from "react";
import { exitLabels } from "../../lib/spec";
import type { Step } from "../../lib/types";
import { cn, formatCost, formatMs, formatTokens } from "../../lib/utils";
import { useStepInfo } from "../../state/catalog";
import { useCheck } from "../../state/check";
import { useFlow } from "../../state/flow";
import { useRun } from "../../state/run";
import { useUi } from "../../state/ui";
import { Tooltip } from "../ui";
import { colorsFor, iconFor, stepSummary } from "./stepMeta";

function useStepView(id: string) {
  const step = useFlow((s) => s.spec?.steps.find((x) => x.id === id));
  const info = useStepInfo(step?.type);
  const run = useRun((s) => s.steps[id]);
  const allIssues = useCheck((s) => s.issues);
  const upstream = useCheck((s) => s.analysis?.upstream[id]);
  const mode = useUi((s) => s.mode);
  const issues = useMemo(() => allIssues.filter((i) => i.step === id), [allIssues, id]);
  return { step, info, run, issues, upstream, mode };
}

/** Open a flow used as a Sub-flow. */
export function openSubflow(step: Step | undefined) {
  if (step?.type === "subflow" && step.settings.flow) window.location.hash = `#/flows/${step.settings.flow}`;
}

function StatusBadges({ id }: { id: string }) {
  const { run, issues } = useStepView(id);
  const errors = issues.filter((i) => i.level === "error");
  const warnings = issues.filter((i) => i.level === "warning");
  return (
    <div className="flex items-center gap-1">
      {run?.status === "running" && <Loader2 size={14} className="animate-spin text-accent" aria-label="Running" />}
      {run?.status === "done" && <CheckCircle2 size={14} className="text-ok" aria-label="Done" />}
      {run?.status === "error" && <CircleAlert size={14} className="text-danger" aria-label="Failed" />}
      {run?.status === "waiting" && <Hand size={14} className="text-warn" aria-label="Waiting for an answer" />}
      {!run && errors.length > 0 && (
        <Tooltip content={errors.map((e) => e.message).join("\n")}>
          <span className="flex items-center gap-0.5 text-[11px] font-semibold text-danger">
            <CircleAlert size={13} />
            {errors.length}
          </span>
        </Tooltip>
      )}
      {!run && errors.length === 0 && warnings.length > 0 && (
        <Tooltip content={warnings.map((e) => e.message).join("\n")}>
          <span className="flex items-center gap-0.5 text-[11px] font-semibold text-warn">
            <AlertTriangle size={13} />
            {warnings.length}
          </span>
        </Tooltip>
      )}
    </div>
  );
}

function Breakpoints({ id }: { id: string }) {
  const before = useUi((s) => s.breakpoints.before.includes(id));
  const after = useUi((s) => s.breakpoints.after.includes(id));
  const paused = useRun((s) => s.pauseReason === "breakpoint" && s.next.includes(id));
  return (
    <>
      {before && (
        <span
          className={cn(
            "absolute top-1/2 -left-4 h-2.5 w-2.5 -translate-y-1/2 rounded-full bg-danger ring-2 ring-surface",
            paused && "animate-pulse",
          )}
          title="Test runs pause before this step"
          data-testid={`breakpoint-before-${id}`}
        />
      )}
      {after && (
        <span
          className="absolute top-1/2 -right-4 h-2.5 w-2.5 -translate-y-1/2 rounded-full bg-danger ring-2 ring-surface"
          title="Test runs pause after this step"
          data-testid={`breakpoint-after-${id}`}
        />
      )}
    </>
  );
}

function RunFooter({ id }: { id: string }) {
  const run = useRun((s) => s.steps[id]);
  const setRightTab = useUi((s) => s.setRightTab);
  if (!run) return null;
  if (run.status === "error" && run.error) {
    return <div className="border-t border-danger/30 bg-danger-soft px-3 py-1.5 text-[11px] leading-snug text-danger">{run.error.message}</div>;
  }
  if (run.status === "waiting") {
    return (
      <div className="flex items-center justify-between gap-2 border-t border-warn/30 bg-warn-soft px-3 py-1.5 text-[11px] text-text">
        <span>Waiting for an answer</span>
        <button type="button" className="nodrag font-semibold text-accent underline" onClick={() => setRightTab("run")}>
          Answer
        </button>
      </div>
    );
  }
  const tail = run.tokens.length > 140 ? `…${run.tokens.slice(-140)}` : run.tokens;
  const items = run.items ? Object.values(run.items) : null;
  return (
    <div className="space-y-1 border-t border-border px-3 py-1.5">
      {run.progress && run.progress.total > 0 && (
        <div className="space-y-0.5" data-testid={`progress-${id}`}>
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
            <div
              className="h-full rounded-full bg-accent transition-all"
              style={{
                width: `${(100 * run.progress.done) / run.progress.total}%`,
              }}
            />
          </div>
          <p className="text-[10.5px] text-muted">
            {run.progress.done} of {run.progress.total} done
          </p>
        </div>
      )}
      {items && (
        <p className="text-[10.5px] text-muted">
          {items.filter((i) => i.status === "done").length} of {items.length} item(s)
        </p>
      )}
      {run.status === "running" && tail && (
        <p className="line-clamp-3 font-mono text-[10.5px] leading-snug whitespace-pre-wrap text-muted" aria-live="polite">
          {tail}
          <span className="animate-pulse">▍</span>
        </p>
      )}
      {run.status === "done" && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10.5px] text-muted" data-testid={`badges-${id}`}>
          <span>{formatMs(run.durationMs)}</span>
          {run.usage && (
            <span>
              {formatTokens(run.usage.input_tokens)}→{formatTokens(run.usage.output_tokens)} tok
            </span>
          )}
          {run.cost != null && <span>{formatCost(run.cost)}</span>}
          {run.exit && <span className="font-medium text-accent">↳ {run.exit}</span>}
        </div>
      )}
    </div>
  );
}

function SubflowLink({ step }: { step: Step }) {
  if (step.type !== "subflow" || !step.settings.flow) return null;
  return (
    <button
      type="button"
      className="nodrag mt-1 flex items-center gap-1 text-[11px] font-medium text-accent hover:underline"
      onClick={() => openSubflow(step)}
      title="Open this flow (or double-click the step)"
    >
      <ExternalLink size={11} /> Open the sub-flow
    </button>
  );
}

export const StepNode = memo(function StepNode({ id, selected }: NodeProps) {
  const { step, info, run, issues, upstream, mode } = useStepView(id);
  if (!step) return null;
  const Icon = iconFor(info?.icon);
  const colors = colorsFor(info?.category);
  const hasError = run?.status === "error" || (!run && issues.some((i) => i.level === "error"));
  return (
    <div
      className={cn(
        "step-card relative w-[220px] rounded-xl border bg-surface shadow-sm transition-shadow",
        hasError ? "border-danger/60" : "border-border",
        run?.status === "running" && "step-running",
        run?.status === "waiting" && "border-warn/70",
        selected && "ring-2 ring-accent",
      )}
      data-testid={`step-${id}`}
      data-status={run?.status ?? "idle"}
      onDoubleClick={() => openSubflow(step)}
    >
      <Breakpoints id={id} />
      {step.type !== "input" && <Handle type="target" position={Position.Left} title="Connect into this step" />}
      <div className="overflow-hidden rounded-[11px]">
        <div className="flex items-start gap-2.5 px-3 pt-2.5 pb-2">
          <div className={cn("mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg", colors.chip)}>
            <Icon size={15} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-1">
              <p className="truncate text-[13px] leading-tight font-semibold" title={step.name || step.id}>
                {step.name || step.id}
              </p>
              <StatusBadges id={id} />
            </div>
            {(step.name !== info?.label || mode === "pro") && (
              <p className="truncate text-[11px] text-faint">
                {info?.label}
                {mode === "pro" && <span className="font-mono"> · {step.id}</span>}
              </p>
            )}
            <p className="mt-1 line-clamp-2 text-[11.5px] leading-snug text-muted">{stepSummary(step, upstream)}</p>
            <SubflowLink step={step} />
          </div>
        </div>
        <RunFooter id={id} />
      </div>
      {step.type !== "output" && <Handle type="source" position={Position.Right} title="Drag to connect to the next step" />}
    </div>
  );
});

/** A step that leaves by labelled exits: Decision, Ask a Human, For Each and Jump. */
export const DecisionNode = memo(function DecisionNode({ id, selected }: NodeProps) {
  const { step, info, run, issues, upstream, mode } = useStepView(id);
  if (!step) return null;
  const Icon = iconFor(info?.icon);
  const colors = colorsFor(info?.category);
  const exits = exitLabels(step);
  const hasError = run?.status === "error" || (!run && issues.some((i) => i.level === "error"));
  const diamond = step.type === "decision";
  const fallback = step.type === "decision" || step.type === "jump";
  return (
    <div
      className={cn(
        "step-card relative w-[240px] overflow-visible rounded-xl border bg-surface shadow-sm",
        hasError ? "border-danger/60" : "border-border",
        run?.status === "running" && "step-running",
        run?.status === "waiting" && "border-warn/70",
        selected && "ring-2 ring-accent",
      )}
      data-testid={`step-${id}`}
      data-status={run?.status ?? "idle"}
    >
      <Breakpoints id={id} />
      <Handle type="target" position={Position.Left} title="Connect into this step" />
      {step.type === "for_each" && <Handle type="target" id="results" position={Position.Bottom} isConnectable={false} className="!opacity-0" />}
      <div className="flex items-center gap-3 px-3 pt-3 pb-2">
        {diamond ? (
          <div className="relative flex h-10 w-10 shrink-0 items-center justify-center" aria-hidden>
            <div className="absolute inset-1 rotate-45 rounded-md bg-amber-500/15 ring-1 ring-amber-500/40" />
            <Icon size={16} className="relative text-amber-600 dark:text-amber-400" />
          </div>
        ) : (
          <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", colors.chip)}>
            <Icon size={16} />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-1">
            <p className="truncate text-[13px] font-semibold" title={step.name || step.id}>
              {step.name || step.id}
            </p>
            <StatusBadges id={id} />
          </div>
          {(step.name !== info?.label || mode === "pro") && !diamond && (
            <p className="truncate text-[11px] text-faint">
              {info?.label}
              {mode === "pro" && <span className="font-mono"> · {step.id}</span>}
            </p>
          )}
          <p className="truncate text-[11px] text-muted">{stepSummary(step, upstream)}</p>
        </div>
      </div>
      <div className="space-y-1 pb-2.5">
        {exits.map((label, i) => {
          const taken = run?.exit === label;
          return (
            <div key={`${label}-${i}`} className="relative flex items-center justify-end pr-4">
              <span
                className={cn(
                  "max-w-[180px] truncate rounded-full px-2 py-0.5 text-[11px]",
                  taken ? "bg-accent text-accent-text" : "bg-surface-2 text-muted",
                  fallback && i === exits.length - 1 && !taken && "italic",
                )}
              >
                {label}
              </span>
              <Handle
                type="source"
                position={Position.Right}
                id={`exit:${label}`}
                title={`Exit: ${label}. Drag to connect`}
                className="!-right-1.5"
              />
            </div>
          );
        })}
        {!exits.length && <Handle type="source" position={Position.Right} title="Drag to connect to the next step" />}
      </div>
      <RunFooter id={id} />
    </div>
  );
});
