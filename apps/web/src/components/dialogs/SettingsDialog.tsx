import { CheckCircle2, ChevronDown, ChevronRight, ExternalLink, KeyRound, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { ProviderInfo, SecretInfo } from "../../lib/types";
import { useCatalog } from "../../state/catalog";
import { useUi, type SettingsTab } from "../../state/ui";
import { Badge, Button, Dialog, Input, Tabs, TabsContent, TabsList, TabsTrigger } from "../ui";
import { McpSection } from "./McpServers";
import { NotificationsSection } from "./Notifications";

function KeyRow({
  env,
  label,
  url,
  set,
  source,
  focus,
  onSaved,
  secretValue,
}: {
  env: string;
  label: string;
  url?: string | null;
  set: boolean;
  source?: string | null;
  focus: boolean;
  onSaved: () => void;
  /** Plain settings (an endpoint URL) show what you type. */
  secretValue?: boolean;
}) {
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
          type={secretValue === false ? "text" : "password"}
          autoComplete="off"
          autoFocus={focus}
          placeholder={set ? "Paste a new value to replace it" : secretValue === false ? "Type it here" : "Paste your key"}
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

const POPULAR = new Set(["openai", "anthropic", "google_genai", "ollama"]);

function ProviderBlock({ p, focus, onSaved, secrets }: { p: ProviderInfo; focus: boolean; onSaved: () => void; secrets: SecretInfo[] }) {
  return (
    <div className="space-y-1.5" data-testid={`provider-${p.id}`}>
      {p.installed === false && (
        <p className="rounded-md bg-warn-soft px-2.5 py-1.5 text-xs text-text">
          {p.label} isn't installed on this server. Run <span className="font-mono">{p.install}</span>.
        </p>
      )}
      {p.key_env && <KeyRow env={p.key_env} label={p.key_label} url={p.key_url} set={p.key_set} source={p.key_source} focus={focus} onSaved={onSaved} />}
      {(p.settings ?? []).map((setting) => (
        <KeyRow
          key={setting.env}
          env={setting.env}
          label={setting.label}
          set={setting.set || secrets.some((s) => s.name === setting.env)}
          focus={false}
          onSaved={onSaved}
          secretValue={false}
        />
      ))}
      {p.credentials && (
        <div className="rounded-lg border border-border p-3 text-xs">
          <p className="text-sm font-medium">{p.label}</p>
          <p className="mt-1 text-muted">{p.credentials}</p>
        </div>
      )}
    </div>
  );
}

function KeysSection() {
  const settings = useUi((s) => s.settings);
  const providers = useCatalog((s) => s.catalog?.providers ?? []);
  const refresh = useCatalog((s) => s.refreshProviders);
  const [secrets, setSecrets] = useState<SecretInfo[]>([]);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [more, setMore] = useState(!!settings.provider && !POPULAR.has(settings.provider));

  const load = () => api.secrets().then(setSecrets).catch(() => setSecrets([]));
  useEffect(() => {
    void load();
    setName(settings.secret ?? "");
  }, [settings.secret]);

  const saved = () => {
    void refresh();
    void load();
  };
  const settingEnvs = new Set(providers.flatMap((p) => [p.key_env, ...(p.settings ?? []).map((s) => s.env)]));
  const others = secrets.filter((s) => !settingEnvs.has(s.name));
  const popular = providers.filter((p) => POPULAR.has(p.id));
  const rest = providers.filter((p) => !POPULAR.has(p.id));

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold">
          <KeyRound size={14} /> AI model keys
        </h3>
        {popular.map((p) => (
          <ProviderBlock key={p.id} p={p} focus={settings.provider === p.id} onSaved={saved} secrets={secrets} />
        ))}
        <p className="text-xs text-muted">
          Ollama runs models on your computer and needs no key. Install it from ollama.com, then run <span className="font-mono">ollama pull llama3.2</span>.
        </p>
        <button type="button" className="flex items-center gap-1 pt-1 text-xs font-medium text-muted hover:text-text" aria-expanded={more} onClick={() => setMore(!more)}>
          {more ? <ChevronDown size={14} /> : <ChevronRight size={14} />} More providers ({rest.length})
        </button>
        {more && (
          <div className="space-y-2">
            {rest.map((p) => (
              <ProviderBlock key={p.id} p={p} focus={settings.provider === p.id} onSaved={saved} secrets={secrets} />
            ))}
          </div>
        )}
      </section>
      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Other secrets</h3>
        <p className="text-xs text-muted">
          Use them in Web request headers, database URLs and MCP servers as <span className="font-mono">{"{secret:NAME}"}</span>.
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
  );
}

export function SettingsDialog() {
  const settings = useUi((s) => s.settings);
  const close = useUi((s) => s.closeSettings);
  const [tab, setTab] = useState<SettingsTab>("keys");
  useEffect(() => {
    if (settings.open) setTab(settings.tab ?? "keys");
  }, [settings.open, settings.tab]);

  return (
    <Dialog open={settings.open} onOpenChange={(o) => !o && close()} title="Settings" description="Keys and secrets are encrypted on this machine and never saved in flows or exports.">
      <Tabs value={tab} onValueChange={(v) => setTab(v as SettingsTab)}>
        <TabsList>
          <TabsTrigger value="keys">Keys and providers</TabsTrigger>
          <TabsTrigger value="mcp">MCP servers</TabsTrigger>
          <TabsTrigger value="notifications">Notifications</TabsTrigger>
        </TabsList>
        <TabsContent value="keys" className="pt-4">
          {settings.open && tab === "keys" && <KeysSection />}
        </TabsContent>
        <TabsContent value="mcp" className="pt-4">
          {settings.open && tab === "mcp" && <McpSection />}
        </TabsContent>
        <TabsContent value="notifications" className="pt-4">
          {settings.open && tab === "notifications" && <NotificationsSection />}
        </TabsContent>
      </Tabs>
    </Dialog>
  );
}
