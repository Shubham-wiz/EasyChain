import { ChevronDown, ChevronRight, History, Loader2, Play, Star } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { EnvironmentId, TestRunDetail, TestRunSummary } from "../../lib/types";
import { timeAgo } from "../../lib/utils";
import { useCan } from "../../state/auth";
import { Badge, Button, Select } from "../ui";

const CHANGE_TONE = { better: "ok", worse: "danger", same: "neutral", new: "accent", gone: "warn" } as const;
const CHANGE_LABEL = { better: "Better", worse: "Worse", same: "Same", new: "New", gone: "Not run" } as const;
const ENV_LABEL: Record<EnvironmentId, string> = { dev: "Development", staging: "Staging", production: "Production" };

function percent(rate: number | undefined) {
  return rate == null ? "–" : `${Math.round(rate * 100)}%`;
}

function signed(value: number | null | undefined, unit: string, digits = 1) {
  if (value == null || value === 0) return null;
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}${unit}`;
}

function RunDetail({ id, canEdit, onBaseline }: { id: string; canEdit: boolean; onBaseline: () => void }) {
  const [detail, setDetail] = useState<TestRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api
      .testRun(id)
      .then(setDetail)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [id]);
  if (error) return <p className="text-xs text-danger">{error}</p>;
  if (!detail) return <Loader2 size={13} className="animate-spin" />;
  const comparison = detail.comparison;
  return (
    <div className="space-y-2 text-xs" data-testid={`test-run-${id}`}>
      {comparison ? (
        <p className="text-muted">
          Against the baseline: {comparison.better} better, {comparison.worse} worse, {comparison.same} the same
          {comparison.new ? `, ${comparison.new} new` : ""}
          {comparison.gone ? `, ${comparison.gone} not run` : ""}.
        </p>
      ) : (
        <p className="text-muted">{detail.baseline ? "This is the baseline: other runs are compared with it." : "There's no baseline to compare with yet."}</p>
      )}
      <ul className="space-y-1" aria-label="Cases in this run">
        {detail.cases.map((c) => {
          const row = comparison?.cases.find((r) => r.name === c.name);
          const deltas = row ? [signed(row.seconds, " s"), signed(row.cost, " $", 4)].filter(Boolean).join(", ") : "";
          return (
            <li key={c.name} className="rounded-md border border-border p-1.5">
              <div className="flex items-center gap-1.5">
                <span className="min-w-0 flex-1 truncate">{c.name}</span>
                {c.flaky && <Badge tone="warn">Flaky</Badge>}
                <span className="tabular-nums">{c.runs > 1 ? `${c.passed}/${c.runs}` : c.passed ? "Passed" : "Failed"}</span>
                {row && <Badge tone={CHANGE_TONE[row.change]}>{CHANGE_LABEL[row.change]}</Badge>}
              </div>
              {row && row.change !== "same" && row.change !== "new" && (
                <p className="text-muted">
                  {percent(row.before)} → {percent(row.after)}
                  {deltas && ` · ${deltas}`}
                </p>
              )}
              {row && row.change === "same" && deltas && <p className="text-muted">{deltas}</p>}
              {c.failures.length > 0 && (
                <ul className="mt-0.5 text-danger">
                  {c.failures.map((f) => (
                    <li key={f}>{f}</li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      {canEdit && !detail.baseline && detail.status === "done" && (
        <Button
          size="sm"
          variant="outline"
          onClick={async () => {
            await api.makeBaseline(id);
            onBaseline();
          }}
        >
          <Star size={12} /> Make this the baseline
        </Button>
      )}
    </div>
  );
}

/** Test Runs that are kept: each against one flow version, compared with the baseline (5.2). */
export function TestRuns({ flowId, disabled, variables }: { flowId: string; disabled: boolean; variables: Record<string, string> }) {
  const canRun = useCan("runner");
  const canEdit = useCan("editor");
  const [runs, setRuns] = useState<TestRunSummary[]>([]);
  const [repeat, setRepeat] = useState(1);
  const [environment, setEnvironment] = useState<EnvironmentId | "">("");
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const load = useCallback(() => {
    api
      .testRuns(flowId)
      .then(setRuns)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [flowId]);
  useEffect(load, [load]);
  const busy = runs.some((r) => r.status === "queued" || r.status === "running");
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(load, 1000);
    return () => window.clearInterval(timer);
  }, [busy, load]);

  return (
    <section className="space-y-2 border-t border-border pt-3" aria-label="Kept Test Runs">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold">
        <History size={14} /> Kept Test Runs
      </h3>
      <p className="text-xs text-muted">
        Run the saved flow and keep the results, to compare later versions with a baseline. Repeat cases to see which ones are flaky with real models. Run in an environment
        to use its secrets and swaps; a run in Staging where every case passes lets the version be published there.
      </p>
      {canRun && (
        <div className="flex flex-wrap items-center gap-2">
          <Select aria-label="Run in" className="h-8 w-40 text-xs" value={environment} onChange={(e) => setEnvironment(e.target.value as EnvironmentId | "")}>
            <option value="">As drawn</option>
            {(Object.keys(ENV_LABEL) as EnvironmentId[]).map((env) => (
              <option key={env} value={env}>
                In {ENV_LABEL[env]}
              </option>
            ))}
          </Select>
          <Select aria-label="Repeat each case" className="h-8 w-36 text-xs" value={repeat} onChange={(e) => setRepeat(Number(e.target.value))}>
            {[1, 3, 5, 10].map((n) => (
              <option key={n} value={n}>
                {n === 1 ? "Each case once" : `Each case ${n} times`}
              </option>
            ))}
          </Select>
          <Button
            size="sm"
            variant="outline"
            disabled={disabled || starting}
            onClick={async () => {
              setStarting(true);
              setError(null);
              try {
                const made = await api.startTestRun(flowId, { repeat, variables, ...(environment ? { environment } : {}) });
                setOpen(made.id);
                load();
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
              } finally {
                setStarting(false);
              }
            }}
          >
            {starting ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Run and keep
          </Button>
        </div>
      )}
      {error && (
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      )}
      {runs.length === 0 && <p className="text-xs text-faint">None yet.</p>}
      <ul className="space-y-1.5" aria-label="Test Runs">
        {runs.map((r) => (
          <li key={r.id} className="rounded-lg border border-border">
            <button type="button" className="flex w-full items-center gap-1.5 p-2 text-left text-xs" aria-expanded={open === r.id} onClick={() => setOpen(open === r.id ? null : r.id)}>
              {open === r.id ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              <span className="min-w-0 flex-1 truncate">
                {timeAgo(r.created_at)}
                {r.by ? ` by ${r.by}` : ""}
                {r.repeat > 1 ? ` · ×${r.repeat}` : ""}
                {r.stand_in ? " · stand-in" : ""}
                {r.environment ? ` · ${ENV_LABEL[r.environment]}` : ""}
              </span>
              {r.baseline && <Badge tone="accent">Baseline</Badge>}
              {r.status === "done" && r.summary ? (
                <Badge tone={r.summary.failed ? "danger" : "ok"}>
                  {r.summary.passed}/{r.summary.total} passed
                </Badge>
              ) : r.status === "error" ? (
                <Badge tone="danger">Didn't finish</Badge>
              ) : (
                <span className="flex items-center gap-1 text-muted">
                  <Loader2 size={12} className="animate-spin" /> {r.progress.done}/{r.progress.total}
                </span>
              )}
            </button>
            {r.status === "error" && r.error && <p className="px-2 pb-2 text-xs text-danger">{r.error}</p>}
            {open === r.id && r.status === "done" && (
              <div className="border-t border-border p-2">
                <RunDetail key={`${r.id}:${r.baseline}`} id={r.id} canEdit={canEdit} onBaseline={load} />
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
