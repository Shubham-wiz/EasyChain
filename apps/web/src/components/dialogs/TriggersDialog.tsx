import { CalendarClock, Check, Copy, FileUp, Link2, Plus, Trash2, Workflow } from "lucide-react";
import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { FlowListItem, Trigger, TriggerKind } from "../../lib/types";
import { timeAgo } from "../../lib/utils";
import { useFlow } from "../../state/flow";
import { useUi } from "../../state/ui";
import { Badge, Button, Dialog, Field, Input, Select, Switch } from "../ui";

const KINDS: { kind: TriggerKind; label: string; help: string; icon: typeof Link2 }[] = [
  { kind: "webhook", label: "Webhook", help: "Another app POSTs JSON to a private URL; its fields become the inputs.", icon: Link2 },
  { kind: "schedule", label: "Schedule", help: "Runs on a timetable, like every weekday at 9:00.", icon: CalendarClock },
  { kind: "upload", label: "File upload", help: "Someone uploads a file to a private URL; the flow gets the file.", icon: FileUp },
  { kind: "after_flow", label: "After another flow", help: "Runs when another flow finishes, with its results as inputs.", icon: Workflow },
];

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button
      size="icon-sm"
      variant="ghost"
      aria-label={label}
      onClick={async () => {
        await navigator.clipboard?.writeText(text).catch(() => undefined);
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      }}
    >
      {done ? <Check size={13} /> : <Copy size={13} />}
    </Button>
  );
}

function describe(t: Trigger, flows: FlowListItem[]): string {
  const cfg = t.config as Record<string, unknown>;
  if (t.kind === "schedule") return `${t.describe ?? cfg.cron} (${cfg.timezone ?? "UTC"})`;
  if (t.kind === "after_flow") {
    const source = flows.find((f) => f.id === cfg.source_flow_id)?.name ?? String(cfg.source_flow_id);
    return `After “${source}” ${((cfg.on as string[]) ?? ["ok"]).includes("error") ? "finishes or fails" : "finishes"}`;
  }
  if (t.kind === "upload") return `Puts the file in \`${cfg.field}\``;
  return "POST JSON to the URL";
}

function TriggerRow({ trigger, flows, onChange }: { trigger: Trigger; flows: FlowListItem[]; onChange: () => void }) {
  const info = KINDS.find((k) => k.kind === trigger.kind)!;
  const Icon = info.icon;
  const url = trigger.url ? `${window.location.origin}${trigger.url}` : null;
  const curl =
    trigger.kind === "upload"
      ? `curl -X POST "${url}?filename=report.pdf" -H "X-Easychain-Token: ${trigger.token}" --data-binary @report.pdf`
      : `curl -X POST "${url}" -H "X-Easychain-Token: ${trigger.token}" -H "Content-Type: application/json" -d '{"question": "Hello"}'`;
  return (
    <li className="space-y-2 rounded-lg border border-border p-3" data-testid={`trigger-${trigger.kind}`}>
      <div className="flex items-center gap-2">
        <Icon size={15} className="text-accent" />
        <span className="text-sm font-medium">{trigger.name || info.label}</span>
        <span className="min-w-0 flex-1 truncate text-xs text-muted">{describe(trigger, flows)}</span>
        <Switch
          checked={trigger.enabled}
          label={trigger.enabled ? "Switch off" : "Switch on"}
          onCheckedChange={async (enabled) => {
            await api.updateTrigger(trigger.id, { enabled });
            onChange();
          }}
        />
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Delete trigger"
          onClick={async () => {
            await api.deleteTrigger(trigger.id);
            onChange();
          }}
        >
          <Trash2 size={13} />
        </Button>
      </div>
      {url && (
        <div className="space-y-1 text-xs">
          <div className="flex items-center gap-1">
            <code className="min-w-0 flex-1 truncate rounded bg-surface-2 px-1.5 py-1 font-mono text-[11px]" data-testid="trigger-url">
              {url}
            </code>
            <CopyButton text={url} label="Copy the URL" />
          </div>
          <div className="flex items-center gap-1">
            <code className="min-w-0 flex-1 truncate rounded bg-surface-2 px-1.5 py-1 font-mono text-[11px]">Token: {trigger.token}</code>
            <CopyButton text={trigger.token} label="Copy the token" />
          </div>
          <div className="flex items-center gap-1">
            <code className="min-w-0 flex-1 truncate rounded bg-surface-2 px-1.5 py-1 font-mono text-[11px] text-muted">{curl}</code>
            <CopyButton text={curl} label="Copy the curl command" />
          </div>
        </div>
      )}
      <p className="text-[11px] text-faint">
        {trigger.next_fire_at ? `Next run ${new Date(trigger.next_fire_at * 1000).toLocaleString()}. ` : ""}
        {trigger.last_fired_at ? `Last ran ${timeAgo(trigger.last_fired_at)}.` : "Hasn't run yet."}
      </p>
    </li>
  );
}

function NewTrigger({ flowId, flows, onCreated }: { flowId: string; flows: FlowListItem[]; onCreated: () => void }) {
  const spec = useFlow((s) => s.spec);
  const [kind, setKind] = useState<TriggerKind>("webhook");
  const [cron, setCron] = useState("0 9 * * 1-5");
  const [timezone, setTimezone] = useState(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [inputs, setInputs] = useState("{}");
  const [field, setField] = useState("");
  const [source, setSource] = useState("");
  const [onError, setOnError] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputFields = ((spec?.steps.find((s) => s.type === "input")?.settings.fields ?? []) as { name: string; type: string }[]).map((f) => f.name);

  const create = async () => {
    setError(null);
    const config: Record<string, unknown> = {};
    if (kind === "schedule") {
      config.cron = cron;
      config.timezone = timezone;
      try {
        config.inputs_values = JSON.parse(inputs || "{}");
      } catch {
        return setError("The inputs need to be JSON, like {\"topic\": \"news\"}.");
      }
    }
    if (kind === "upload") config.field = field || inputFields[0];
    if (kind === "after_flow") {
      config.source_flow_id = source;
      config.on = onError ? ["ok", "error"] : ["ok"];
    }
    try {
      await api.createTrigger({ flow_id: flowId, kind, config });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  return (
    <div className="space-y-3 rounded-lg border border-dashed border-border p-3">
      <div className="grid grid-cols-2 gap-1.5">
        {KINDS.map((k) => (
          <button
            key={k.kind}
            type="button"
            aria-pressed={kind === k.kind}
            onClick={() => setKind(k.kind)}
            className={`flex items-start gap-2 rounded-lg border p-2 text-left text-xs ${kind === k.kind ? "border-accent bg-accent-soft" : "border-border hover:bg-surface-2"}`}
          >
            <k.icon size={14} className="mt-0.5 shrink-0 text-accent" />
            <span>
              <span className="block font-medium">{k.label}</span>
              <span className="text-muted">{k.help}</span>
            </span>
          </button>
        ))}
      </div>
      {kind === "schedule" && (
        <div className="grid grid-cols-2 gap-2">
          <Field label="When (cron)" help="minute hour day month weekday. “0 9 * * 1-5” is 9:00 on weekdays; “*/15 * * * *” every 15 minutes." htmlFor="trigger-cron">
            <Input id="trigger-cron" className="font-mono" value={cron} onChange={(e) => setCron(e.target.value)} />
          </Field>
          <Field label="Time zone" htmlFor="trigger-tz">
            <Input id="trigger-tz" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
          </Field>
          <div className="col-span-2">
            <Field label="Inputs for each run (JSON)" htmlFor="trigger-inputs">
              <Input id="trigger-inputs" className="font-mono text-xs" value={inputs} onChange={(e) => setInputs(e.target.value)} />
            </Field>
          </div>
        </div>
      )}
      {kind === "upload" && (
        <Field label="Put the file in" help="An Input field (best as type File); it gets the saved file's path." htmlFor="trigger-field">
          <Select id="trigger-field" value={field || inputFields[0] || ""} onChange={(e) => setField(e.target.value)}>
            {inputFields.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </Select>
        </Field>
      )}
      {kind === "after_flow" && (
        <div className="space-y-2">
          <Field label="After this flow" htmlFor="trigger-source">
            <Select id="trigger-source" value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="">Pick a flow</option>
              {flows
                .filter((f) => f.id !== flowId)
                .map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
            </Select>
          </Field>
          <label className="flex items-center gap-2 text-xs">
            <Switch checked={onError} onCheckedChange={setOnError} label="Also when it fails" /> Also when it fails
          </label>
          <p className="text-[11px] text-muted">Its results go in as inputs with the same names.</p>
        </div>
      )}
      {error && <p className="text-xs text-danger">{error}</p>}
      <Button variant="primary" size="sm" onClick={create} data-testid="create-trigger">
        <Plus size={13} /> Add the trigger
      </Button>
    </div>
  );
}

export function TriggersDialog() {
  const open = useUi((s) => s.triggersOpen);
  const setOpen = useUi((s) => s.setTriggersOpen);
  const flowId = useFlow((s) => s.flowId);
  const [triggers, setTriggers] = useState<Trigger[] | null>(null);
  const [flows, setFlows] = useState<FlowListItem[]>([]);
  const [adding, setAdding] = useState(false);
  const load = () => {
    if (!flowId) return;
    api.triggers(flowId).then(setTriggers).catch(() => setTriggers([]));
  };
  useEffect(() => {
    if (!open) return;
    load();
    api.flows().then(setFlows).catch(() => setFlows([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, flowId]);
  if (!flowId) return null;
  return (
    <Dialog open={open} onOpenChange={setOpen} title="Triggers" description="Start this flow from other apps, on a schedule, from a file upload or after another flow.">
      <div className="space-y-3">
        {triggers && triggers.length > 0 && (
          <ul className="space-y-2">
            {triggers.map((t) => (
              <TriggerRow key={t.id} trigger={t} flows={flows} onChange={load} />
            ))}
          </ul>
        )}
        {triggers && !triggers.length && !adding && <p className="text-sm text-muted">No triggers yet. Runs start from the Run panel or the API.</p>}
        {adding || (triggers && !triggers.length) ? (
          <NewTrigger
            flowId={flowId}
            flows={flows}
            onCreated={() => {
              setAdding(false);
              load();
            }}
          />
        ) : (
          <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
            <Plus size={13} /> New trigger
          </Button>
        )}
        <p className="text-[11px] text-faint">
          <Badge>API</Badge> You can always start a run with <span className="font-mono">POST /api/runs</span> and follow it with{" "}
          <span className="font-mono">/api/runs/&lt;id&gt;/events</span>.
        </p>
      </div>
    </Dialog>
  );
}
