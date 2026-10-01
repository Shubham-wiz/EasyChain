import { ArrowLeft, Hand, Inbox as InboxIcon, Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { InboxItem } from "../lib/types";
import { cn, timeAgo } from "../lib/utils";
import { AnswerForm, type Answer } from "./run/AnswerForm";
import { Badge, Button } from "./ui";

/** How many runs are waiting for someone (for badges). */
export function useInboxCount(): number {
  const [count, setCount] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .inbox()
        .then((items) => alive && setCount(items.length))
        .catch(() => undefined);
    void load();
    const t = setInterval(load, 10_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);
  return count;
}

function Item({ item, highlight, onDone }: { item: InboxItem; highlight: boolean; onDone: (message: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const answer = async (a: Answer) => {
    setBusy(true);
    setError(null);
    try {
      await api.answer(item.id, a);
      onDone(`Answered “${item.flow_name}”. The run carries on.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setBusy(false);
    }
  };
  return (
    <li
      id={`inbox-${item.id}`}
      className={cn("space-y-3 rounded-xl border bg-surface p-4 shadow-sm", highlight ? "border-accent ring-2 ring-accent/30" : "border-border")}
      data-testid="inbox-item"
    >
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <Hand size={14} className="text-warn" />
        <span className="font-semibold text-text">{item.flow_name}</span>
        <span>·</span>
        <span className="font-mono">{[...item.path, item.step].join(" › ")}</span>
        <span className="ml-auto">{timeAgo(item.created_at)}</span>
      </div>
      <AnswerForm request={item.request} onAnswer={answer} busy={busy} testId={`inbox-answer-${item.id}`} />
      {error && <p className="text-xs text-danger">{error}</p>}
      {item.flow_id && (
        <a className="text-xs text-accent hover:underline" href={`#/flows/${item.flow_id}`}>
          Open the flow
        </a>
      )}
    </li>
  );
}

export function Inbox({ focus, onHome }: { focus: string | null; onHome: () => void }) {
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [answered, setAnswered] = useState<InboxItem[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const load = useCallback(() => {
    api.inbox().then(setItems).catch(() => setItems([]));
    api.inbox("answered").then((list) => setAnswered(list.slice(0, 8))).catch(() => setAnswered([]));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 8_000);
    return () => clearInterval(t);
  }, [load]);
  useEffect(() => {
    if (focus && items) document.getElementById(`inbox-${focus}`)?.scrollIntoView({ block: "center" });
  }, [focus, items]);

  return (
    <div className="h-full overflow-y-auto">
      <header className="sticky top-0 z-10 border-b border-border bg-surface/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-3xl items-center gap-3 px-6">
          <Button variant="ghost" size="icon-sm" aria-label="All flows" onClick={onHome}>
            <ArrowLeft size={16} />
          </Button>
          <InboxIcon size={18} className="text-accent" />
          <h1 className="text-base font-semibold">Inbox</h1>
          {items && <Badge tone={items.length ? "warn" : "neutral"}>{items.length} waiting</Badge>}
          <Button variant="ghost" size="sm" className="ml-auto" onClick={load}>
            <RefreshCw size={13} /> Refresh
          </Button>
        </div>
      </header>
      <main className="mx-auto max-w-3xl space-y-4 px-6 py-6">
        <p className="text-sm text-muted">Runs that stopped at an Ask a Human step wait here, for as long as it takes. Your answer picks the run up where it stopped.</p>
        {notice && (
          <p role="status" className="rounded-lg bg-ok-soft px-3 py-2 text-sm text-ok">
            {notice}
          </p>
        )}
        {!items && <Loader2 className="animate-spin text-muted" size={18} />}
        {items && !items.length && (
          <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted" data-testid="inbox-empty">
            Nothing is waiting for you.
          </div>
        )}
        <ul className="space-y-3">
          {items?.map((item) => (
            <Item
              key={item.id}
              item={item}
              highlight={item.id === focus}
              onDone={(message) => {
                setNotice(message);
                setItems((list) => list?.filter((x) => x.id !== item.id) ?? null);
                setTimeout(load, 600);
              }}
            />
          ))}
        </ul>
        {answered.length > 0 && (
          <section className="pt-4">
            <h2 className="mb-2 text-xs font-semibold tracking-wide text-faint uppercase">Answered recently</h2>
            <ul className="divide-y divide-border rounded-lg border border-border text-xs">
              {answered.map((a) => (
                <li key={a.id} className="flex items-center gap-2 px-3 py-2">
                  <span className="font-medium">{a.flow_name}</span>
                  <span className="min-w-0 flex-1 truncate text-muted">{a.request.question}</span>
                  <span className="text-faint">{a.answered_at ? timeAgo(a.answered_at) : ""}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>
    </div>
  );
}
