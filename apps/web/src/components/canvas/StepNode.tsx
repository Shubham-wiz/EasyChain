import { Handle, Position, type NodeProps } from "@xyflow/react";
import { AlertTriangle, CheckCircle2, CircleAlert, Loader2 } from "lucide-react";
import { memo, useMemo } from "react";
import { exitLabels } from "../../lib/spec";
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

function StatusBadges({ id }: { id: string }) {
  const { run, issues } = useStepView(id);
  const errors = issues.filter((i) => i.level === "error");
  const warnings = issues.filter((i) => i.level === "warning");
  return (
    <div className="flex items-center gap-1">
      {run?.status === "running" && <Loader2 size={14} className="animate-spin text-accent" aria-label="Running" />}
      {run?.status === "done" && <CheckCircle2 size={14} className="text-ok" aria-label="Done" />}
      {run?.status === "error" && <CircleAlert size={14} className="text-danger" aria-label="Failed" />}
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

function RunFooter({ id }: { id: string }) {
  const run = useRun((s) => s.steps[id]);
  if (!run) return null;
  if (run.status === "error" && run.error) {
    return <div className="border-t border-danger/30 bg-danger-soft px-3 py-1.5 text-[11px] leading-snug text-danger">{run.error.message}</div>;
  }
  const tail = run.tokens.length > 140 ? `…${run.tokens.slice(-140)}` : run.tokens;
  return (
    <div className="space-y-1 border-t border-border px-3 py-1.5">
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

export const StepNode = memo(function StepNode({ id, selected }: NodeProps) {
  const { step, info, run, issues, upstream, mode } = useStepView(id);
  if (!step) return null;
  const Icon = iconFor(info?.icon);
  const colors = colorsFor(info?.category);
  const hasError = run?.status === "error" || (!run && issues.some((i) => i.level === "error"));
  return (
    <div
      className={cn(
        "step-card w-[220px] overflow-hidden rounded-xl border bg-surface shadow-sm transition-shadow",
        hasError ? "border-danger/60" : "border-border",
        run?.status === "running" && "step-running",
        selected && "ring-2 ring-accent",
      )}
      data-testid={`step-${id}`}
      data-status={run?.status ?? "idle"}
    >
      {step.type !== "input" && <Handle type="target" position={Position.Left} aria-label={`Into ${step.name}`} />}
      <div className="flex items-start gap-2.5 px-3 pt-2.5 pb-2">
        <div className={cn("mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg", colors.chip)}>
          <Icon size={15} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-1">
            <p className="truncate text-[13px] leading-tight font-semibold">{step.name || step.id}</p>
            <StatusBadges id={id} />
          </div>
          {(step.name !== info?.label || mode === "pro") && (
            <p className="truncate text-[11px] text-faint">
              {info?.label}
              {mode === "pro" && <span className="font-mono"> · {step.id}</span>}
            </p>
          )}
          <p className="mt-1 line-clamp-2 text-[11.5px] leading-snug text-muted">{stepSummary(step, upstream)}</p>
        </div>
      </div>
      <RunFooter id={id} />
      {step.type !== "output" && <Handle type="source" position={Position.Right} aria-label={`Out of ${step.name}`} />}
    </div>
  );
});

export const DecisionNode = memo(function DecisionNode({ id, selected }: NodeProps) {
  const { step, info, run, issues, upstream } = useStepView(id);
  if (!step) return null;
  const Icon = iconFor(info?.icon);
  const exits = exitLabels(step);
  const hasError = run?.status === "error" || (!run && issues.some((i) => i.level === "error"));
  return (
    <div
      className={cn(
        "step-card w-[240px] overflow-visible rounded-xl border bg-surface shadow-sm",
        hasError ? "border-danger/60" : "border-border",
        run?.status === "running" && "step-running",
        selected && "ring-2 ring-accent",
      )}
      data-testid={`step-${id}`}
      data-status={run?.status ?? "idle"}
    >
      <Handle type="target" position={Position.Left} aria-label={`Into ${step.name}`} />
      <div className="flex items-center gap-3 px-3 pt-3 pb-2">
        <div className="relative flex h-10 w-10 shrink-0 items-center justify-center" aria-hidden>
          <div className="absolute inset-1 rotate-45 rounded-md bg-amber-500/15 ring-1 ring-amber-500/40" />
          <Icon size={16} className="relative text-amber-600 dark:text-amber-400" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-1">
            <p className="truncate text-[13px] font-semibold">{step.name || step.id}</p>
            <StatusBadges id={id} />
          </div>
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
                  i === exits.length - 1 && !taken && "italic",
                )}
              >
                {label}
              </span>
              <Handle
                type="source"
                position={Position.Right}
                id={`exit:${label}`}
                aria-label={`Exit ${label}`}
                className="!-right-1.5"
              />
            </div>
          );
        })}
      </div>
      <RunFooter id={id} />
    </div>
  );
});
