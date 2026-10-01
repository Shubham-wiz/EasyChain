import { ArrowRight, CheckCircle2, FilePlus2, KeyRound, Loader2, Moon, Play, Sun, Trash2, Upload, Workflow } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { FlowListItem, TemplateInfo } from "../lib/types";
import { timeAgo } from "../lib/utils";
import { useUi } from "../state/ui";
import { SettingsDialog } from "./dialogs/SettingsDialog";
import { InboxLink } from "./TopBar";
import { Badge, Button } from "./ui";

export function Home({ open }: { open: (flowId: string, opts?: { tryIt?: boolean }) => void }) {
  const [flows, setFlows] = useState<FlowListItem[] | null>(null);
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const { theme, toggleTheme, openSettings } = useUi();

  const load = () => {
    api.flows().then(setFlows).catch((e) => setError(String(e.message ?? e)));
    api.templates().then(setTemplates).catch(() => setTemplates([]));
  };
  useEffect(load, []);

  const create = async (key: string, body: Parameters<typeof api.createFlow>[0], tryIt = false) => {
    setBusy(key);
    setError(null);
    try {
      const { id } = await api.createFlow(body);
      open(id, { tryIt });
    } catch (e) {
      setError(e instanceof ApiError ? [e.message, ...e.problems].join(" ") : String(e));
      setBusy(null);
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <header className="sticky top-0 z-10 border-b border-border bg-surface/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-6">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="text-base font-semibold">Easy Chain</span>
          <div className="ml-auto flex items-center gap-1">
            <InboxLink />
            <Button variant="ghost" size="sm" onClick={() => openSettings()}>
              <KeyRound size={14} /> API keys
            </Button>
            <Button variant="ghost" size="icon-sm" aria-label="Toggle theme" onClick={toggleTheme}>
              {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-10 px-6 py-10">
        <section className="flex flex-wrap items-end justify-between gap-6">
          <div className="max-w-2xl space-y-2">
            <h1 className="text-3xl font-semibold tracking-tight">Draw your AI app, press Run, watch it think.</h1>
            <p className="text-muted">
              Build LLM apps and agents by connecting steps. Every flow runs on LangGraph and exports as clean LangChain code you can ship.
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => fileRef.current?.click()}>
              <Upload size={15} /> Open a flow file
            </Button>
            <Button variant="primary" onClick={() => create("blank", { name: "My flow" })} disabled={busy !== null} data-testid="new-flow">
              {busy === "blank" ? <Loader2 size={15} className="animate-spin" /> : <FilePlus2 size={15} />} New blank flow
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept=".yaml,.yml"
              className="hidden"
              aria-label="Flow file"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (file) await create("import", { yaml: await file.text() });
                e.target.value = "";
              }}
            />
          </div>
        </section>

        {error && (
          <p role="alert" className="rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}

        <section aria-labelledby="templates-heading" className="space-y-3">
          <h2 id="templates-heading" className="text-lg font-semibold">
            Start from a template
          </h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {templates.map((t) => (
              <article key={t.id} className="flex flex-col rounded-xl border border-border bg-surface p-4 shadow-sm" data-testid={`template-${t.id}`}>
                <p className="text-[11px] font-medium tracking-wide text-faint uppercase">{t.category}</p>
                <h3 className="mt-1 font-semibold">{t.name}</h3>
                <p className="mt-1 flex-1 text-sm leading-relaxed text-muted">{t.description}</p>
                <div className="mt-3 flex flex-wrap gap-1">
                  {t.keys.map((k) => (
                    <Badge key={k.env} tone={k.set ? "ok" : "warn"}>
                      {k.set ? <CheckCircle2 size={10} /> : <KeyRound size={10} />} {k.label}
                    </Badge>
                  ))}
                  {!t.keys.length && <Badge tone="ok">No key needed</Badge>}
                </div>
                <div className="mt-3 flex gap-2">
                  <Button size="sm" variant="primary" disabled={busy !== null} onClick={() => create(`try-${t.id}`, { template: t.id }, true)}>
                    {busy === `try-${t.id}` ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Try it
                  </Button>
                  <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => create(`use-${t.id}`, { template: t.id })}>
                    Use template
                  </Button>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section aria-labelledby="flows-heading" className="space-y-3">
          <h2 id="flows-heading" className="text-lg font-semibold">
            Your flows
          </h2>
          {flows === null ? (
            <p className="text-sm text-muted">
              <Loader2 size={14} className="mr-1 inline animate-spin" /> Loading…
            </p>
          ) : !flows.length ? (
            <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted">
              <Workflow className="mx-auto mb-2 text-faint" />
              No flows yet. Start from a template or a blank canvas.
            </div>
          ) : (
            <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-surface">
              {flows.map((f) => (
                <li key={f.id} className="flex items-center gap-3 px-4 py-3 hover:bg-surface-2/60">
                  <button type="button" className="min-w-0 flex-1 text-left" onClick={() => open(f.id)}>
                    <p className="truncate font-medium">{f.name}</p>
                    <p className="truncate text-xs text-muted">{f.problem ?? (f.description || `${f.steps} steps`)}</p>
                  </button>
                  <span className="text-xs text-faint">{timeAgo(f.updated)}</span>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Delete ${f.name}`}
                    onClick={async () => {
                      if (!window.confirm(`Delete “${f.name}”? This can't be undone.`)) return;
                      await api.deleteFlow(f.id);
                      load();
                    }}
                  >
                    <Trash2 size={14} />
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => open(f.id)}>
                    Open <ArrowRight size={13} />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
      <SettingsDialog />
    </div>
  );
}
