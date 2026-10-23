import { Check, Copy, KeyRound, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { SsoProvider, SsoSettings } from "../../lib/types";
import { useAuth } from "../../state/auth";
import { Button, Field, Input, Select } from "../ui";

const BLANK: SsoProvider = { id: "", name: "", issuer: "", client_id: "", client_secret: "", new_people: "none", domains: [] };

function ProviderForm({ value, onChange, onRemove, fixed }: { value: SsoProvider; onChange: (p: SsoProvider) => void; onRemove: () => void; fixed: boolean }) {
  const roles = useAuth((s) => s.status?.roles) ?? [];
  const [copied, setCopied] = useState(false);
  const key = value.id || "new";
  const set = (patch: Partial<SsoProvider>) => onChange({ ...value, ...patch });
  return (
    <fieldset className="space-y-2 rounded-lg border border-border p-3" disabled={fixed} data-testid={`sso-${key}`}>
      <legend className="sr-only">{value.name || "A new provider"}</legend>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Name on the sign-in page" htmlFor={`sso-name-${key}`}>
          <Input id={`sso-name-${key}`} placeholder="Okta" value={value.name} onChange={(e) => set({ name: e.target.value })} />
        </Field>
        <Field label="Id (in its address)" htmlFor={`sso-id-${key}`}>
          <Input id={`sso-id-${key}`} placeholder="okta" className="font-mono" value={value.id} onChange={(e) => set({ id: e.target.value.toLowerCase() })} />
        </Field>
      </div>
      <Field label="Issuer" htmlFor={`sso-issuer-${key}`}>
        <Input id={`sso-issuer-${key}`} placeholder="https://your-company.okta.com" value={value.issuer} onChange={(e) => set({ issuer: e.target.value })} />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Client id" htmlFor={`sso-client-${key}`}>
          <Input id={`sso-client-${key}`} value={value.client_id} onChange={(e) => set({ client_id: e.target.value })} />
        </Field>
        <Field label="Client secret" htmlFor={`sso-secret-${key}`}>
          <Input id={`sso-secret-${key}`} type="password" autoComplete="off" placeholder="kept in the secrets vault" value={value.client_secret} onChange={(e) => set({ client_secret: e.target.value })} />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="People signing in for the first time" htmlFor={`sso-new-${key}`}>
          <Select id={`sso-new-${key}`} value={value.new_people} onChange={(e) => set({ new_people: e.target.value as SsoProvider["new_people"] })}>
            <option value="none">Can't (an Owner adds them first)</option>
            {roles.map((r) => (
              <option key={r.id} value={r.id}>
                Get an account as {r.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Only these email domains" htmlFor={`sso-domains-${key}`}>
          <Input id={`sso-domains-${key}`} placeholder="example.com, example.org" value={value.domains.join(", ")} onChange={(e) => set({ domains: e.target.value.split(",").map((d) => d.trim()).filter(Boolean) })} />
        </Field>
      </div>
      {value.callback && (
        <div className="flex items-center gap-1.5 text-[11px] text-muted">
          <span>Give {value.name || "the provider"} this redirect address:</span>
          <code className="truncate rounded bg-surface-2 px-1 font-mono">{value.callback}</code>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Copy the redirect address"
            onClick={async () => {
              await navigator.clipboard?.writeText(value.callback!).catch(() => undefined);
              setCopied(true);
            }}
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </Button>
        </div>
      )}
      <Button size="sm" variant="ghost" onClick={onRemove}>
        <Trash2 size={12} /> Remove {value.name || "this provider"}
      </Button>
    </fieldset>
  );
}

/** Owners: signing in with the company's identity provider (OpenID Connect). */
export function SsoSection() {
  const [cfg, setCfg] = useState<SsoSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    api
      .sso()
      .then(setCfg)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  if (!cfg) return error ? <p className="text-xs text-danger">{error}</p> : null;
  const fixed = cfg.source === "environment";
  const update = (providers: SsoProvider[]) => {
    setCfg({ ...cfg, providers });
    setSaved(false);
  };
  return (
    <section className="space-y-3" aria-label="Single sign-on">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold">
        <KeyRound size={14} /> Single sign-on
      </h3>
      <p className="text-xs text-muted">
        Let people sign in with your company's identity provider (OpenID Connect: Okta, Google, Microsoft Entra, Keycloak…). Make an app there first; docs/sso.md says how
        for each. Passwords keep working.
      </p>
      {fixed && (
        <p className="rounded-lg border border-border bg-surface-2 px-3 py-2 text-xs text-muted" role="note">
          The server's environment sets this (<span className="font-mono">EASYCHAIN_SSO</span>), so it can't be changed here.
        </p>
      )}
      {cfg.providers.map((p, i) => (
        <ProviderForm
          key={i}
          value={p}
          fixed={fixed}
          onChange={(next) => update(cfg.providers.map((q, j) => (j === i ? next : q)))}
          onRemove={() => update(cfg.providers.filter((_, j) => j !== i))}
        />
      ))}
      {error && (
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      )}
      {!fixed && (
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => update([...cfg.providers, { ...BLANK }])}>
            <Plus size={13} /> Add a provider
          </Button>
          <Button
            size="sm"
            variant="primary"
            onClick={async () => {
              try {
                setCfg(await api.saveSso(cfg.providers));
                setError(null);
                setSaved(true);
              } catch (err) {
                setError(err instanceof Error ? err.message : String(err));
              }
            }}
          >
            {saved ? <Check size={13} /> : null} {saved ? "Saved" : "Save single sign-on"}
          </Button>
        </div>
      )}
    </section>
  );
}
