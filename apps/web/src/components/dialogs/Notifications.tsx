import { Bell, Check, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { NotificationSettings } from "../../lib/types";
import { Button, DraftInput, Field, Input, SecretInput, Switch } from "../ui";

/** Where to tell people that a run is waiting for them (Ask a Human). */
export function NotificationsSection() {
  const [cfg, setCfg] = useState<NotificationSettings | null>(null);
  const [state, setState] = useState<"idle" | "saving" | "saved" | "testing">("idle");
  const [results, setResults] = useState<{ channel: string; ok: boolean; error?: string }[] | null>(null);
  useEffect(() => {
    api.notifications().then(setCfg).catch(() => setCfg(null));
  }, []);
  if (!cfg) return null;
  const save = async (next: NotificationSettings) => {
    setState("saving");
    setCfg(await api.saveNotifications(next));
    setState("saved");
  };
  const patch = <K extends keyof NotificationSettings>(key: K, value: Partial<NotificationSettings[K]>) =>
    setCfg({ ...cfg, [key]: typeof cfg[key] === "object" ? { ...(cfg[key] as object), ...(value as object) } : value } as NotificationSettings);
  return (
    <section className="space-y-3" aria-label="Notifications">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold">
        <Bell size={14} /> Notifications
      </h3>
      <p className="text-xs text-muted">When a run waits at an Ask a Human step, tell people here. Use {"{secret:NAME}"} for webhook URLs and passwords.</p>
      <div className="space-y-2 rounded-lg border border-border p-3">
        <label className="flex items-center gap-2 text-sm font-medium">
          <Switch checked={cfg.webhook.enabled} onCheckedChange={(v) => patch("webhook", { enabled: v })} label="Webhook" /> Webhook
        </label>
        {cfg.webhook.enabled && (
          <Input aria-label="Webhook URL" placeholder="https://… (gets a JSON POST)" value={cfg.webhook.url} onChange={(e) => patch("webhook", { url: e.target.value })} />
        )}
      </div>
      <div className="space-y-2 rounded-lg border border-border p-3">
        <label className="flex items-center gap-2 text-sm font-medium">
          <Switch checked={cfg.slack.enabled} onCheckedChange={(v) => patch("slack", { enabled: v })} label="Slack" /> Slack
        </label>
        {cfg.slack.enabled && (
          <SecretInput
            aria-label="Slack webhook URL"
            placeholder="https://hooks.slack.com/services/… or {secret:SLACK_WEBHOOK}"
            value={cfg.slack.webhook_url}
            onChange={(webhook_url) => patch("slack", { webhook_url })}
          />
        )}
      </div>
      <div className="space-y-2 rounded-lg border border-border p-3">
        <label className="flex items-center gap-2 text-sm font-medium">
          <Switch checked={cfg.email.enabled} onCheckedChange={(v) => patch("email", { enabled: v })} label="Email" /> Email
        </label>
        {cfg.email.enabled && (
          <div className="grid grid-cols-2 gap-2">
            <Field label="SMTP server" htmlFor="smtp-host">
              <Input id="smtp-host" value={cfg.email.smtp_host} onChange={(e) => patch("email", { smtp_host: e.target.value })} />
            </Field>
            <Field label="Port" htmlFor="smtp-port">
              <Input id="smtp-port" type="number" value={cfg.email.smtp_port} onChange={(e) => patch("email", { smtp_port: Number(e.target.value) })} />
            </Field>
            <Field label="User name" htmlFor="smtp-user">
              <Input id="smtp-user" value={cfg.email.username} onChange={(e) => patch("email", { username: e.target.value })} />
            </Field>
            <Field label="Password" help="Best as {secret:NAME}, a secret from Settings › Keys and providers." htmlFor="smtp-password">
              <SecretInput id="smtp-password" placeholder="{secret:SMTP_PASSWORD}" value={cfg.email.password} onChange={(password) => patch("email", { password })} />
            </Field>
            <Field label="From" htmlFor="smtp-from">
              <Input id="smtp-from" value={cfg.email.sender} onChange={(e) => patch("email", { sender: e.target.value })} />
            </Field>
            <Field label="To (commas between)" htmlFor="smtp-to">
              <DraftInput
                id="smtp-to"
                value={cfg.email.to.join(", ")}
                onCommit={(text) => patch("email", { to: text.split(",").map((s) => s.trim()).filter(Boolean) })}
              />
            </Field>
            <label className="col-span-2 flex items-center gap-2 text-xs">
              <Switch checked={cfg.email.starttls} onCheckedChange={(v) => patch("email", { starttls: v })} label="Use STARTTLS" /> Use STARTTLS
            </label>
          </div>
        )}
      </div>
      <Field label="Link back to this app" help="Notifications link to the Inbox at this address." htmlFor="public-url">
        <Input id="public-url" placeholder={window.location.origin} value={cfg.public_url} onChange={(e) => patch("public_url", e.target.value as never)} />
      </Field>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="primary" onClick={() => void save(cfg)} disabled={state === "saving"}>
          {state === "saving" ? <Loader2 size={13} className="animate-spin" /> : state === "saved" ? <Check size={13} /> : null} Save
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={async () => {
            await save(cfg);
            setState("testing");
            setResults((await api.testNotifications()).results);
            setState("idle");
          }}
        >
          Send a test
        </Button>
      </div>
      {results && (
        <ul className="space-y-0.5 text-xs">
          {!results.length && <li className="text-muted">Nothing is switched on.</li>}
          {results.map((r) => (
            <li key={r.channel} className={r.ok ? "text-ok" : "text-danger"}>
              {r.channel}: {r.ok ? "sent" : r.error}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
