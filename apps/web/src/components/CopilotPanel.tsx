// The describe-it copilot: describe a flow or a change; review the proposal on the canvas; keep
// the changes you want (one undo step).

import { CircleAlert, Loader2, Sparkles, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useCopilot } from "../state/copilot";
import { useFlow } from "../state/flow";
import { useUi } from "../state/ui";
import { IssueList } from "./inspector/Inspector";
import { ModelPicker } from "./inspector/fields";
import { Badge, Button, Textarea } from "./ui";

const EXAMPLES = [
  "Summarise a web page in three bullet points",
  "Sort feedback into complaints and praise, and draft a reply to each",
  "An agent that looks up an order by its number",
];

function ModelLine() {
  const [using, setUsing] = useState<string | null | undefined>(undefined);
  const [chosen, setChosen] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const openSettings = useUi((s) => s.openSettings);
  useEffect(() => {
    api
      .copilotSettings()
      .then((s) => {
        setUsing(s.using);
        setChosen(s.model);
      })
      .catch(() => setUsing(null));
  }, []);
  const save = (model: string | null) =>
    api.saveCopilotSettings(model).then((s) => {
      setUsing(s.using);
      setChosen(s.model);
    });
  if (using === undefined) return null;
  return (
    <div className="space-y-1.5 text-xs text-muted" data-testid="copilot-model">
      {using ? (
        <p>
          Uses <span className="font-mono">{using}</span>
          {!chosen && " (the best model you have a key for)"}.{" "}
          <button type="button" className="font-medium text-accent hover:underline" onClick={() => setOpen(!open)}>
            Change
          </button>
        </p>
      ) : (
        <p>
          No API key yet, so the <strong>stand-in copilot</strong> shows an example from the templates.{" "}
          <button type="button" className="font-medium text-accent hover:underline" onClick={() => openSettings()}>
            Add a key
          </button>
        </p>
      )}
      {open && (
        <div className="space-y-1.5 rounded-lg border border-border p-2">
          <ModelPicker
            field={{ key: "model", label: "Copilot model", kind: "model" }}
            id="copilot-model-picker"
            value={chosen ?? using ?? ""}
            onChange={(v) => void save(String(v))}
            settings={{}}
            fields={[]}
          />
          {chosen && (
            <button type="button" className="text-[11px] text-accent hover:underline" onClick={() => void save(null)}>
              Use the best model I have a key for
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function CopilotPanel() {
  const { request, setRequest, busy, error, proposal, kept, keptCases, ask, toggle, toggleCase, accept, discard } = useCopilot();
  const hasSteps = useFlow((s) => (s.spec?.steps.length ?? 0) > 2);
  return (
    <div className="scroll-thin h-full space-y-4 overflow-y-auto p-4" data-testid="copilot-panel">
      <div className="space-y-1">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold">
          <Sparkles size={14} className="text-violet-500" /> Copilot
        </h2>
        <p className="text-xs leading-relaxed text-muted">
          Describe {hasSteps ? "what to change" : "the flow you want"} in your own words. The copilot proposes the steps, settings and Flow Data, checks
          them, and shows them on the canvas before anything changes.
        </p>
        <ModelLine />
      </div>
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (request.trim()) void ask(request);
        }}
      >
        <Textarea
          aria-label="Describe the flow"
          rows={4}
          value={request}
          placeholder={hasSteps ? "Also save a one-line summary, and send it to Slack" : EXAMPLES[0]}
          onChange={(e) => setRequest(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || !(e.metaKey || e.ctrlKey)) return;
            // Not the editor's Ctrl+Enter (run the flow).
            e.preventDefault();
            e.stopPropagation();
            if (request.trim() && !busy) void ask(request);
          }}
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" type="submit" disabled={busy || !request.trim()} data-testid="copilot-propose">
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />} Propose
          </Button>
          {!hasSteps &&
            !request &&
            EXAMPLES.slice(1).map((example) => (
              <button key={example} type="button" className="text-left text-[11px] text-accent hover:underline" onClick={() => setRequest(example)}>
                {example}
              </button>
            ))}
        </div>
      </form>
      {busy && (
        <p className="flex items-center gap-1.5 text-xs text-muted" role="status">
          <Loader2 size={12} className="animate-spin" /> Thinking, then checking the proposal before showing it…
        </p>
      )}
      {error && (
        <p className="flex items-start gap-1.5 text-xs text-danger" role="alert">
          <CircleAlert size={13} className="mt-0.5 shrink-0" /> {error}
        </p>
      )}
      {proposal && !proposal.ok && (
        <div className="space-y-1.5 rounded-lg border border-danger/30 bg-danger-soft p-3 text-xs" role="alert" data-testid="copilot-failed">
          <p>{proposal.summary}</p>
          {proposal.problems.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-4 text-muted">
              {proposal.problems.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
          )}
          <Button size="sm" variant="outline" onClick={discard}>
            OK
          </Button>
        </div>
      )}
      {proposal?.ok && (
        <section className="space-y-3" aria-label="Proposal" data-testid="copilot-proposal">
          <p className="rounded-lg bg-accent-soft/50 px-3 py-2 text-sm leading-relaxed">{proposal.summary}</p>
          <div>
            <div className="mb-1 flex items-center justify-between">
              <h3 className="text-xs font-semibold tracking-wide text-faint uppercase">Changes</h3>
              <Badge>
                {kept.length} of {proposal.changes.length} kept
              </Badge>
            </div>
            {proposal.changes.length ? (
              <ul className="space-y-1">
                {proposal.changes.map((change) => (
                  <li key={change.id}>
                    <label className="flex items-start gap-2 rounded-md border border-border px-2 py-1.5 text-xs hover:bg-surface-2">
                      <input type="checkbox" className="mt-0.5 accent-[var(--accent)]" checked={kept.includes(change.id)} onChange={() => toggle(change.id)} />
                      <span>{change.label}</span>
                    </label>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted">Nothing to change: the flow already does this.</p>
            )}
          </div>
          {proposal.test_cases.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold tracking-wide text-faint uppercase">Test cases</h3>
              <ul className="space-y-1">
                {proposal.test_cases.map((tc, i) => (
                  <li key={i}>
                    <label className="flex items-start gap-2 rounded-md border border-border px-2 py-1.5 text-xs hover:bg-surface-2">
                      <input type="checkbox" className="mt-0.5 accent-[var(--accent)]" checked={keptCases.includes(i)} onChange={() => toggleCase(i)} />
                      <span>
                        {tc.name}
                        <span className="block font-mono text-[11px] text-faint">{JSON.stringify(tc.inputs ?? {})}</span>
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-[11px] text-faint">Kept cases go into the flow's Test Set.</p>
            </div>
          )}
          {proposal.issues.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold tracking-wide text-faint uppercase">Worth a look</h3>
              <IssueList issues={proposal.issues} />
            </div>
          )}
          <div className="flex gap-2">
            <Button variant="primary" onClick={() => void accept()} data-testid="copilot-accept" disabled={!kept.length && !keptCases.length}>
              Apply {kept.length} change{kept.length === 1 ? "" : "s"}
            </Button>
            <Button variant="outline" onClick={discard} data-testid="copilot-discard">
              Discard
            </Button>
          </div>
          <p className="text-[11px] text-faint">The canvas shows the kept changes. Applying them is one step you can undo.</p>
        </section>
      )}
    </div>
  );
}
