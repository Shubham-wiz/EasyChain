import { Check, Copy, KeyRound, MessageCircle, Rocket, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { AccessKey, EnvironmentId, PublishState } from "../../lib/types";
import { timeAgo } from "../../lib/utils";
import { useCan } from "../../state/auth";
import { useFlow } from "../../state/flow";
import { useUi } from "../../state/ui";
import { Badge, Button, Dialog, Input, Select } from "../ui";

const RULES: Record<EnvironmentId, string> = {
  dev: "Takes the saved flow as it is.",
  staging: "Takes a version whose kept Test Run, run in Staging, passed every case.",
  production: "Takes the version that is in Staging. Owners only.",
};

function CopyText({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="flex gap-1.5">
      <Input readOnly aria-label={label} className="h-8 font-mono text-[11px]" value={text} onFocus={(e) => e.target.select()} />
      <Button
        size="icon-sm"
        variant="outline"
        aria-label={`Copy ${label.toLowerCase()}`}
        onClick={async () => {
          await navigator.clipboard?.writeText(text).catch(() => undefined);
          setDone(true);
        }}
      >
        {done ? <Check size={13} /> : <Copy size={13} />}
      </Button>
    </div>
  );
}

function NewKey({ made, flowId }: { made: AccessKey; flowId: string }) {
  const origin = window.location.origin;
  if (made.kind === "chat") {
    return (
      <div className="space-y-1.5 rounded-lg border border-ok bg-ok-soft p-3 text-xs" role="status">
        <p className="font-medium">The chat link “{made.name}” is ready.</p>
        <CopyText label="Chat page address" text={`${origin}${made.chat_path}`} />
        <p>To put a chat button on a site (listed for this link), add:</p>
        <CopyText label="Widget code" text={`<script src="${origin}/widget.js" data-chat="${made.id}"></script>`} />
      </div>
    );
  }
  return (
    <div className="space-y-1.5 rounded-lg border border-ok bg-ok-soft p-3 text-xs" role="status">
      <p className="font-medium">“{made.name}” is ready. Copy it now: it isn't shown again.</p>
      <CopyText label="The new access key" text={made.key ?? ""} />
      <p>Other apps run the flow with it:</p>
      <CopyText
        label="Example call"
        text={`curl -X POST ${origin}/api/published/${flowId}/${made.environment} -H "Authorization: Bearer ${made.key}" -H "Content-Type: application/json" -d '{"inputs": {}}'`}
      />
    </div>
  );
}

/** Publish a flow to an environment, and the keys and chat links that run it (5.4). */
export function PublishDialog() {
  const open = useUi((s) => s.publishOpen);
  const setOpen = useUi((s) => s.setPublishOpen);
  const setRightTab = useUi((s) => s.setRightTab);
  const flowId = useFlow((s) => s.flowId);
  const canEdit = useCan("editor");
  const owner = useCan("owner");
  const [state, setState] = useState<PublishState | null>(null);
  const [error, setError] = useState<{ env?: EnvironmentId; text: string; action?: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState({ environment: "dev" as EnvironmentId, name: "", kind: "secret" as "secret" | "chat", per_minute: 60, origins: "" });
  const [made, setMade] = useState<AccessKey | null>(null);

  const load = useCallback(() => {
    if (!flowId) return;
    api
      .publishState(flowId)
      .then(setState)
      .catch((e) => setError({ text: e instanceof Error ? e.message : String(e) }));
  }, [flowId]);
  useEffect(() => {
    if (open) {
      setMade(null);
      setError(null);
      load();
    }
  }, [open, load]);
  if (!flowId) return null;

  const allowed = (env: EnvironmentId) => (env === "production" ? owner : canEdit);
  const act = async (key: string, env: EnvironmentId | undefined, work: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    try {
      await work();
      load();
    } catch (e) {
      setError({ env, text: e instanceof Error ? e.message : String(e), action: e instanceof ApiError ? e.action : undefined });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen} title="Publish" description="Give a version of this flow a stable address for other apps and a chat page for people." wide>
      <div className="space-y-4">
        <ul className="space-y-2" aria-label="Environments">
          {(state?.environments ?? []).map((env) => (
            <li key={env.id} className="space-y-1.5 rounded-lg border border-border p-3" data-testid={`env-${env.id}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold">{env.label}</span>
                {env.published ? (
                  <Badge tone="ok">
                    Version {env.published.version_id.slice(0, 7)} · {timeAgo(env.published.published_at)}
                    {env.published.published_by ? ` by ${env.published.published_by}` : ""}
                  </Badge>
                ) : (
                  <Badge>Nothing published</Badge>
                )}
                <span className="ml-auto flex gap-1.5">
                  {allowed(env.id) && (
                    <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void act(`publish:${env.id}`, env.id, () => api.publish(flowId, { environment: env.id }))}>
                      <Rocket size={13} /> {env.id === "production" ? "Publish what's in Staging" : `Publish to ${env.label}`}
                    </Button>
                  )}
                  {allowed(env.id) && env.published && (
                    <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void act(`stop:${env.id}`, env.id, () => api.unpublish(flowId, env.id))}>
                      Stop
                    </Button>
                  )}
                </span>
              </div>
              <p className="text-xs text-muted">{RULES[env.id]}</p>
              {error?.env === env.id && (
                <div className="flex flex-wrap items-center gap-2 rounded-md border border-danger/30 bg-danger-soft px-2 py-1.5 text-xs" role="alert">
                  <span className="min-w-0 flex-1">{error.text}</span>
                  {error.action === "test_run" && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setOpen(false);
                        setRightTab("tests");
                      }}
                    >
                      Open Tests
                    </Button>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
        {error && !error.env && (
          <p className="text-xs text-danger" role="alert">
            {error.text}
          </p>
        )}

        <section className="space-y-2" aria-label="Access">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold">
            <KeyRound size={14} /> Access
          </h3>
          <p className="text-xs text-muted">An access key lets another app run what is published to one environment; a chat link gives people a chat page and a widget. Each has a limit of requests a minute.</p>
          <ul className="space-y-1.5" aria-label="Access keys">
            {(state?.keys ?? []).map((k) => (
              <li key={k.id} className="flex flex-wrap items-center gap-2 rounded-md border border-border px-2 py-1.5 text-xs">
                {k.kind === "chat" ? <MessageCircle size={13} /> : <KeyRound size={13} />}
                <span className="font-medium">{k.name}</span>
                <Badge>{state?.environments.find((e) => e.id === k.environment)?.label ?? k.environment}</Badge>
                <span className="text-muted">
                  {k.per_minute}/min · {k.last_used_at ? `used ${timeAgo(k.last_used_at)}` : "not used yet"}
                </span>
                {k.chat_path && (
                  <a className="text-accent underline" href={k.chat_path} target="_blank" rel="noreferrer">
                    Open the chat page
                  </a>
                )}
                {allowed(k.environment) && (
                  <Button size="icon-sm" variant="ghost" className="ml-auto" aria-label={`Revoke ${k.name}`} onClick={() => void act(`revoke:${k.id}`, undefined, () => api.revokeAccessKey(flowId, k.id))}>
                    <Trash2 size={12} />
                  </Button>
                )}
              </li>
            ))}
            {state && state.keys.length === 0 && <li className="text-xs text-faint">No keys or chat links yet.</li>}
          </ul>
          {made && <NewKey made={made} flowId={flowId} />}
          {canEdit && (
            <form
              className="grid grid-cols-2 gap-2 rounded-lg border border-dashed border-border p-3"
              onSubmit={(e) => {
                e.preventDefault();
                void act("key", undefined, async () => {
                  setMade(
                    await api.createAccessKey(flowId, {
                      ...draft,
                      origins: draft.origins
                        .split(/[\s,]+/)
                        .map((o) => o.trim())
                        .filter(Boolean),
                    }),
                  );
                });
              }}
            >
              <Select aria-label="Kind" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as "secret" | "chat" })}>
                <option value="secret">An access key for another app</option>
                <option value="chat">A chat link for people</option>
              </Select>
              <Select aria-label="Environment" value={draft.environment} onChange={(e) => setDraft({ ...draft, environment: e.target.value as EnvironmentId })}>
                {(state?.environments ?? []).map((env) => (
                  <option key={env.id} value={env.id} disabled={!allowed(env.id)}>
                    {env.label}
                  </option>
                ))}
              </Select>
              <Input aria-label="Name" placeholder="What it's for, like Shop backend" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
              <label className="flex items-center gap-2 text-xs text-muted">
                <Input aria-label="Requests a minute" type="number" min={1} className="w-24" value={draft.per_minute} onChange={(e) => setDraft({ ...draft, per_minute: Number(e.target.value) || 1 })} />
                requests a minute
              </label>
              {draft.kind === "chat" && (
                <Input
                  aria-label="Sites that may show the widget"
                  className="col-span-2"
                  placeholder="Sites that may show the widget, like https://shop.example.com"
                  value={draft.origins}
                  onChange={(e) => setDraft({ ...draft, origins: e.target.value })}
                />
              )}
              <Button type="submit" variant="outline" size="sm" className="col-span-2 justify-center" disabled={busy !== null}>
                {draft.kind === "chat" ? "Make the chat link" : "Make the access key"}
              </Button>
            </form>
          )}
        </section>

        {state && state.history.length > 0 && (
          <details>
            <summary className="cursor-pointer text-xs text-muted">History</summary>
            <ul className="mt-1 space-y-0.5 text-xs text-muted">
              {state.history.slice(0, 15).map((h) => (
                <li key={h.id}>
                  {timeAgo(h.published_at)}: version {h.version_id.slice(0, 7)} to {h.environment}
                  {h.published_by ? ` by ${h.published_by}` : ""}
                  {h.retired_at ? " (replaced)" : ""}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </Dialog>
  );
}
