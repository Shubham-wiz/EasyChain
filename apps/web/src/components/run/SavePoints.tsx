import { ChevronDown, ChevronRight, GitBranch, History, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../../lib/api";
import type { SavePoint } from "../../lib/types";
import { cn } from "../../lib/utils";
import { useCheck } from "../../state/check";
import { useFlow } from "../../state/flow";
import { forkFrom, useRun } from "../../state/run";
import { Button, Textarea } from "../ui";

function asText(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function parse(text: string, original: unknown): unknown {
  if (typeof original === "string" || original == null) return text;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** Save Points of the current run: look at the Flow Data at each one and re-run from there. */
export function SavePoints({ runId }: { runId: string }) {
  const [open, setOpen] = useState(false);
  const [points, setPoints] = useState<SavePoint[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const status = useRun((s) => s.status);
  const spec = useFlow((s) => s.spec);
  const fields = useCheck((s) => s.analysis?.fields);
  const hidden = useMemo(() => new Set((fields ?? []).filter((f) => f.private).map((f) => f.name)), [fields]);
  const busy = status === "running" || status === "queued";

  useEffect(() => {
    if (!open || busy) return;
    setPoints(null);
    api
      .savePoints(runId)
      .then((all) => setPoints(all.filter((p) => p.run_id === runId).reverse()))
      .catch(() => setPoints([]));
  }, [open, runId, busy]);

  const point = points?.find((p) => p.checkpoint_id === selected);
  useEffect(() => {
    if (!point) return;
    setDraft(Object.fromEntries(Object.entries(point.values).filter(([k]) => !hidden.has(k)).map(([k, v]) => [k, asText(v)])));
  }, [point, hidden]);

  const stepName = (id: string) => spec?.steps.find((s) => s.id === id)?.name || id.replace(/__done$/, " (collect results)");
  const label = (p: SavePoint) =>
    p.source === "input" ? "Start" : p.next.length ? `Before ${p.next.map(stepName).join(", ")}` : p.waiting.length ? "Waiting for an answer" : "Finished";

  const rerun = () => {
    if (!point) return;
    const update: Record<string, unknown> = {};
    for (const [k, text] of Object.entries(draft)) {
      if (text !== asText(point.values[k])) update[k] = parse(text, point.values[k]);
    }
    void forkFrom(point.checkpoint_id, Object.keys(update).length ? update : null);
  };

  return (
    <section aria-label="Save Points">
      <button type="button" className="flex items-center gap-1.5 text-xs font-medium text-muted hover:text-text" onClick={() => setOpen(!open)} aria-expanded={open}>
        <History size={13} /> Save Points (time travel)
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {busy && <p className="text-xs text-faint">Available when the run stops.</p>}
          {!busy && !points && <Loader2 size={14} className="animate-spin text-muted" />}
          {points && !points.length && <p className="text-xs text-faint">No Save Points for this run.</p>}
          {points && points.length > 0 && (
            <ol className="relative space-y-0.5 border-l border-border pl-3" data-testid="save-points">
              {points.map((p) => (
                <li key={p.checkpoint_id}>
                  <button
                    type="button"
                    className={cn(
                      "w-full rounded px-1.5 py-1 text-left text-xs hover:bg-surface-2",
                      selected === p.checkpoint_id && "bg-accent-soft font-medium text-accent",
                    )}
                    onClick={() => setSelected(selected === p.checkpoint_id ? null : p.checkpoint_id)}
                  >
                    {label(p)}
                    <span className="ml-1 text-faint">{new Date(p.created_at).toLocaleTimeString()}</span>
                  </button>
                </li>
              ))}
            </ol>
          )}
          {point && (
            <div className="space-y-2 rounded-lg border border-border p-2.5" data-testid="save-point-detail">
              <p className="text-xs text-muted">
                Flow Data at this point. Change any value, then run again from here; the original run stays as it was. The re-run starts past
                a breakpoint at this point; later breakpoints still pause it.
              </p>
              {Object.entries(draft).map(([k, v]) => (
                <label key={k} className="block space-y-0.5">
                  <span className="font-mono text-[11px] text-muted">{k}</span>
                  <Textarea
                    aria-label={`Value of ${k}`}
                    rows={Math.min(6, Math.max(1, v.split("\n").length))}
                    className={cn("text-xs", typeof point.values[k] !== "string" && "font-mono")}
                    value={v}
                    onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
                  />
                </label>
              ))}
              {!Object.keys(draft).length && <p className="text-xs text-faint">No Flow Data yet.</p>}
              {point.next.length > 0 && (
                <Button size="sm" variant="primary" onClick={rerun} data-testid="rerun-from-here">
                  <GitBranch size={13} /> Run again from here
                </Button>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
