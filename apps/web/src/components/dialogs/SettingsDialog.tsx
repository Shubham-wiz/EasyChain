import { CheckCircle2, ExternalLink, KeyRound, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { SecretInfo } from "../../lib/types";
import { useCatalog } from "../../state/catalog";
import { useUi } from "../../state/ui";
import { Badge, Button, Dialog, Input } from "../ui";

function KeyRow({ env, label, url, set, source, focus, onSaved }: { env: string; label: string; url?: string | null; set: boolean; source?: string | null; focus: boolean; onSaved: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  return (
    <form
      className="space-y-1.5 rounded-lg border border-border p-3"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!value.trim()) return;
        setSaving(true);
        try {
          await api.setSecret(env, value.trim());
          setValue("");
          setError(null);
          onSaved();
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err));
        } finally {
          setSaving(false);
        }
      }}
    >
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={`key-${env}`} className="text-sm font-medium">
          {label}
        </label>
        {set ? (
          <Badge tone="ok">
            <CheckCircle2 size={11} /> Set{source === "environment" ? " (from environment)" : ""}
          </Badge>
        ) : (
          <Badge tone="warn">Not set</Badge>
        )}
      </div>
      <div className="flex gap-1.5">
        <Input
          id={`key-${env}`}
          type="password"
          autoComplete="off"
          autoFocus={focus}
          placeholder={set ? "Paste a new key to replace it" : "Paste your key"}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <Button type="submit" variant="primary" disabled={!value.trim() || saving}>
          Save
        </Button>
      </div>
      <p className="flex items-center gap-2 text-[11px] text-faint">
        <span className="font-mono">{env}</span>
        {url && (
          <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-accent hover:underline">
            Get a key <ExternalLink size={10} />
          </a>
        )}
      </p>
      {error && <p className="text-xs text-danger">{error}</p>}
    </form>
  );
}

export function SettingsDialog() {
  const settings = useUi((s) => s.settings);
  const close = useUi((s) => s.closeSettings);
  const providers = useCatalog((s) => s.catalog?.providers ?? []);
  const refresh = useCatalog((s) => s.refreshProviders);
  const [secrets, setSecrets] = useState<SecretInfo[]>([]);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = () => api.secrets().then(setSecrets).catch(() => setSecrets([]));
  useEffect(() => {
    if (settings.open) {
      void load();
      setName(settings.secret ?? "");
    }
  }, [settings.open, settings.secret]);

  const keyEnvs = new Set(providers.map((p) => p.key_env));
  const others = secrets.filter((s) => !keyEnvs.has(s.name));

  return (
    <Dialog open={settings.open} onOpenChange={(o) => !o && close()} title="Settings" description="API keys and secrets are encrypted on this machine and never saved in flows or exports.">
      <div className="space-y-6">
        <section className="space-y-2">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold">
            <KeyRound size={14} /> AI model keys
          </h3>
          {providers
            .filter((p) => p.key_env)
            .map((p) => (
              <KeyRow
                key={p.id}
                env={p.key_env!}
                label={p.key_label}
                url={p.key_url}
                set={p.key_set}
                source={p.key_source}
                focus={settings.provider === p.id}
                onSaved={() => {
                  void refresh();
                  void load();
                }}
              />
            ))}
          <p className="text-xs text-muted">
            Ollama runs models on your computer and needs no key. Install it from ollama.com, then run <span className="font-mono">ollama pull llama3.2</span>.
          </p>
        </section>
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">Other secrets</h3>
          <p className="text-xs text-muted">
            Use them in Web request headers as <span className="font-mono">{"{secret:NAME}"}</span>.
          </p>
          <ul className="space-y-1">
            {others.map((s) => (
              <li key={s.name} className="flex items-center justify-between rounded-md border border-border px-2.5 py-1.5 text-sm">
                <span className="font-mono text-[13px]">{s.name}</span>
                <span className="flex items-center gap-2">
                  <Badge>{s.source}</Badge>
                  {s.source === "vault" && (
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      aria-label={`Delete ${s.name}`}
                      onClick={async () => {
                        await api.deleteSecret(s.name);
                        void load();
                      }}
                    >
                      <Trash2 size={13} />
                    </Button>
                  )}
                </span>
              </li>
            ))}
          </ul>
          <form
            className="flex gap-1.5"
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                await api.setSecret(name.trim(), value);
                setName("");
                setValue("");
                setError(null);
                void load();
              } catch (err) {
                setError(err instanceof Error ? err.message : String(err));
              }
            }}
          >
            <Input aria-label="Secret name" placeholder="NAME" className="w-2/5 font-mono uppercase" value={name} autoFocus={!!settings.secret} onChange={(e) => setName(e.target.value.toUpperCase())} />
            <Input aria-label="Secret value" type="password" placeholder="value" value={value} onChange={(e) => setValue(e.target.value)} />
            <Button type="submit" variant="outline" disabled={!name || !value}>
              <Plus size={14} /> Add
            </Button>
          </form>
          {error && <p className="text-xs text-danger">{error}</p>}
        </section>
      </div>
    </Dialog>
  );
}
