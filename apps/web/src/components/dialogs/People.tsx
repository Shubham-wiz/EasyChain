import { Check, Copy, KeyRound, Plus, Trash2, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { ApiKeyInfo, NewApiKey, Person, Role } from "../../lib/types";
import { timeAgo } from "../../lib/utils";
import { atLeast, useAuth, useMe } from "../../state/auth";
import { Badge, Button, Input, Select } from "../ui";

const ROLE_HELP: Record<Role, string> = {
  viewer: "sees flows, runs and the Inbox",
  runner: "and starts runs and answers the Inbox",
  editor: "and changes flows, Knowledge Bases and triggers",
  owner: "and secrets, settings and people",
};

function useRoles() {
  return useAuth((s) => s.status?.roles ?? []);
}

function message(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

function PersonRow({ person, me, reload, onError }: { person: Person; me: string | null; reload: () => void; onError: (m: string | null) => void }) {
  const roles = useRoles();
  const [removing, setRemoving] = useState(false);
  const [password, setPassword] = useState<string | null>(null);
  const change = async (body: Parameters<typeof api.updatePerson>[1]) => {
    try {
      await api.updatePerson(person.id, body);
      onError(null);
      reload();
    } catch (err) {
      onError(message(err));
    }
  };
  return (
    <li className="space-y-2 rounded-lg border border-border p-2.5" data-testid={`person-${person.email}`}>
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">
            {person.name || person.email} {person.id === me && <span className="text-xs font-normal text-faint">(you)</span>}
          </p>
          <p className="truncate text-xs text-muted">
            {person.name && `${person.email} · `}
            {person.last_login_at ? `signed in ${timeAgo(person.last_login_at)}` : "hasn't signed in yet"}
          </p>
        </div>
        {person.disabled && <Badge tone="warn">Switched off</Badge>}
        <Select aria-label={`Role of ${person.email}`} className="h-7 w-28 text-xs" value={person.role} onChange={(e) => void change({ role: e.target.value as Role })}>
          {roles.map((r) => (
            <option key={r.id} value={r.id}>
              {r.label}
            </option>
          ))}
        </Select>
      </div>
      <div className="flex flex-wrap items-center gap-1">
        <Button size="sm" variant="ghost" onClick={() => void change({ disabled: !person.disabled })}>
          {person.disabled ? "Switch on" : "Switch off"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setPassword(password === null ? "" : null)}>
          <KeyRound size={12} /> New password
        </Button>
        {removing ? (
          <span className="flex items-center gap-1 text-xs">
            Remove {person.email}?
            <Button
              size="sm"
              variant="danger"
              onClick={async () => {
                try {
                  await api.removePerson(person.id);
                  onError(null);
                  reload();
                } catch (err) {
                  onError(message(err));
                  setRemoving(false);
                }
              }}
            >
              Remove
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRemoving(false)}>
              Keep
            </Button>
          </span>
        ) : (
          <Button size="sm" variant="ghost" aria-label={`Remove ${person.email}`} onClick={() => setRemoving(true)}>
            <Trash2 size={12} />
          </Button>
        )}
      </div>
      {password !== null && (
        <form
          className="flex gap-1.5"
          onSubmit={async (e) => {
            e.preventDefault();
            await change({ password });
            setPassword(null);
          }}
        >
          <Input type="password" aria-label={`New password for ${person.email}`} placeholder="New password (10+ characters)" autoComplete="new-password" className="h-8" value={password} onChange={(e) => setPassword(e.target.value)} />
          <Button type="submit" size="sm" variant="primary" disabled={!password}>
            Set
          </Button>
        </form>
      )}
    </li>
  );
}

/** Owners: who can sign in, and their roles. */
export function PeopleSection() {
  const me = useMe();
  const roles = useRoles();
  const [people, setPeople] = useState<Person[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState({ email: "", name: "", role: "runner" as Role, password: "" });
  const load = () => {
    api.people().then(setPeople).catch((err) => setError(message(err)));
  };
  useEffect(load, []);
  return (
    <section className="space-y-3" aria-label="People">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold">
        <Users size={14} /> People
      </h3>
      <ul className="space-y-1 text-xs text-muted">
        {roles.map((r) => (
          <li key={r.id}>
            <strong className="text-text">{r.label}</strong> {ROLE_HELP[r.id]}
          </li>
        ))}
      </ul>
      <ul className="space-y-2">
        {people.map((p) => (
          <PersonRow key={p.id} person={p} me={me?.id ?? null} reload={load} onError={setError} />
        ))}
      </ul>
      {error && (
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      )}
      <form
        className="space-y-2 rounded-lg border border-dashed border-border p-3"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await api.addPerson({ ...draft, email: draft.email.trim(), name: draft.name.trim() });
            setDraft({ email: "", name: "", role: draft.role, password: "" });
            setError(null);
            load();
          } catch (err) {
            setError(message(err));
          }
        }}
      >
        <p className="text-sm font-medium">Add someone</p>
        <div className="grid grid-cols-2 gap-2">
          <Input aria-label="Their email" type="email" placeholder="Email" value={draft.email} onChange={(e) => setDraft({ ...draft, email: e.target.value })} />
          <Input aria-label="Their name" placeholder="Name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          <Input
            aria-label="Their first password"
            type="password"
            autoComplete="new-password"
            placeholder="First password"
            value={draft.password}
            onChange={(e) => setDraft({ ...draft, password: e.target.value })}
          />
          <Select aria-label="Their role" value={draft.role} onChange={(e) => setDraft({ ...draft, role: e.target.value as Role })}>
            {roles.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex items-center justify-between gap-2">
          <p className="text-[11px] text-faint">Tell them their first password; they can change it from their account menu.</p>
          <Button type="submit" variant="outline" size="sm" disabled={!draft.email.trim() || !draft.password}>
            <Plus size={13} /> Add
          </Button>
        </div>
      </form>
    </section>
  );
}

function ShownOnce({ made }: { made: NewApiKey }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-1.5 rounded-lg border border-ok bg-ok-soft p-3" role="status">
      <p className="text-sm font-medium">“{made.name}” is ready. Copy it now: it isn't shown again.</p>
      <div className="flex gap-1.5">
        <Input readOnly aria-label="The new key" className="font-mono text-xs" value={made.key} onFocus={(e) => e.target.select()} />
        <Button
          variant="outline"
          onClick={async () => {
            await navigator.clipboard?.writeText(made.key).catch(() => undefined);
            setCopied(true);
          }}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <p className="text-[11px] text-muted">
        Send it as <span className="font-mono">Authorization: Bearer …</span>, or give it to <span className="font-mono">@easychain/client</span> as{" "}
        <span className="font-mono">apiKey</span>.
      </p>
    </div>
  );
}

/** Keys for scripts and other apps; each acts as its person, with at most the role it was made with. */
export function ApiKeysSection() {
  const me = useMe();
  const roles = useRoles().filter((r) => atLeast(me, r.id));
  const [keys, setKeys] = useState<ApiKeyInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role | "">("");
  const [made, setMade] = useState<NewApiKey | null>(null);
  const load = () => {
    api.apiKeys().then(setKeys).catch((err) => setError(message(err)));
  };
  useEffect(load, []);
  return (
    <section className="space-y-3" aria-label="Keys for scripts">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold">
        <KeyRound size={14} /> Keys for scripts
      </h3>
      <p className="text-xs text-muted">
        For scripts and other apps that call Easy Chain. A key acts as you, with at most the role you give it: a Runner key can start and answer runs of saved flows but
        not change them. Keep keys on a server, never in a web page.
      </p>
      {made && <ShownOnce made={made} />}
      <ul className="space-y-1.5">
        {keys.map((k) => (
          <li key={k.id} className="flex items-center justify-between gap-2 rounded-md border border-border px-2.5 py-1.5 text-sm">
            <span className="min-w-0">
              <span className="block truncate font-medium">{k.name}</span>
              <span className="block text-[11px] text-faint">
                {!k.mine && "someone else's · "}
                made {timeAgo(k.created_at)} · {k.last_used_at ? `used ${timeAgo(k.last_used_at)}` : "not used yet"}
              </span>
            </span>
            <span className="flex items-center gap-1.5">
              <Badge>{roles.find((r) => r.id === k.role)?.label ?? k.role}</Badge>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Revoke ${k.name}`}
                onClick={async () => {
                  try {
                    await api.revokeApiKey(k.id);
                    if (made?.id === k.id) setMade(null);
                    load();
                  } catch (err) {
                    setError(message(err));
                  }
                }}
              >
                <Trash2 size={13} /> Revoke
              </Button>
            </span>
          </li>
        ))}
        {!keys.length && <li className="text-xs text-faint">No keys yet.</li>}
      </ul>
      <form
        className="flex gap-1.5"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            setMade(await api.createApiKey({ name: name.trim() || "API key", role: role || undefined }));
            setName("");
            setError(null);
            load();
          } catch (err) {
            setError(message(err));
          }
        }}
      >
        <Input aria-label="What the key is for" placeholder="What it's for, e.g. Support site" value={name} onChange={(e) => setName(e.target.value)} />
        <Select aria-label="The key's role" className="w-32" value={role} onChange={(e) => setRole(e.target.value as Role | "")}>
          <option value="">{me ? `${me.role_label} (yours)` : "Your role"}</option>
          {roles
            .filter((r) => r.id !== me?.role)
            .map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
        </Select>
        <Button type="submit" variant="outline">
          <Plus size={14} /> Make a key
        </Button>
      </form>
      {error && (
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
