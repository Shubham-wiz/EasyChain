import { History, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { AuditEntry, Person } from "../../lib/types";
import { timeAgo } from "../../lib/utils";
import { useAuth } from "../../state/auth";
import { Badge, Button, Select } from "../ui";

const VIA: Record<AuditEntry["via"], string> = {
  session: "signed in",
  key: "key",
  local: "this computer",
  trigger: "trigger",
  cli: "command line",
  none: "not signed in",
};

/** Owners: who changed what, and when (newest first). Nothing in it can be changed or removed. */
export function AuditSection() {
  const signIn = useAuth((s) => s.status?.mode === "on");
  const [kind, setKind] = useState("");
  const [person, setPerson] = useState("");
  const [people, setPeople] = useState<Person[]>([]);
  const [kinds, setKinds] = useState<{ id: string; label: string }[]>([]);
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (signIn) api.people().then(setPeople).catch(() => setPeople([]));
  }, [signIn]);
  useEffect(() => {
    setEntries(null);
    api
      .audit({ kind, person, limit: 50 })
      .then((page) => {
        setEntries(page.entries);
        setNext(page.next);
        setKinds(page.kinds);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [kind, person]);

  const older = async () => {
    if (next === null) return;
    const page = await api.audit({ kind, person, before: next, limit: 50 });
    setEntries([...(entries ?? []), ...page.entries]);
    setNext(page.next);
  };

  return (
    <section className="space-y-3" aria-label="Audit log">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold">
        <History size={14} /> Audit log
      </h3>
      <p className="text-xs text-muted">
        Who changed what, and when: flows, runs and Inbox answers, Knowledge Bases, skills, triggers, secrets and settings, people and keys, and signing in. Nothing
        in it can be changed or removed. <span className="font-mono">easychain audit --json</span> exports it.
      </p>
      <div className="flex gap-1.5">
        <Select aria-label="What changed" className="h-8 text-xs" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">Everything</option>
          {kinds.map((k) => (
            <option key={k.id} value={k.id}>
              {k.label}
            </option>
          ))}
        </Select>
        {signIn && (
          <Select aria-label="Who" className="h-8 text-xs" value={person} onChange={(e) => setPerson(e.target.value)}>
            <option value="">Everyone</option>
            {people.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name || p.email}
              </option>
            ))}
          </Select>
        )}
      </div>
      {error && (
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      )}
      {entries === null && !error && <Loader2 size={16} className="animate-spin text-muted" />}
      {entries && !entries.length && <p className="text-xs text-faint">Nothing yet.</p>}
      <ol className="divide-y divide-border rounded-lg border border-border" data-testid="audit-entries">
        {(entries ?? []).map((e) => (
          <li key={e.id} className="flex items-start gap-2 px-2.5 py-1.5 text-xs">
            <span className="min-w-0 flex-1 break-words">{e.text}</span>
            <Badge>{VIA[e.via] ?? e.via}</Badge>
            <time className="w-16 shrink-0 text-right text-faint" dateTime={new Date(e.at * 1000).toISOString()} title={new Date(e.at * 1000).toLocaleString()}>
              {timeAgo(e.at)}
            </time>
          </li>
        ))}
      </ol>
      {next !== null && (
        <Button size="sm" variant="outline" onClick={() => void older()}>
          Show older
        </Button>
      )}
    </section>
  );
}
