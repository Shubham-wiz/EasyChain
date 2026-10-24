import { Activity, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { TracingSettings, TracingState } from "../../lib/types";
import { useCan } from "../../state/auth";
import { useUi } from "../../state/ui";
import { Button, Input, Locked, Switch } from "../ui";

function message(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

function TracingForm({ state }: { state: TracingState }) {
  const owner = useCan("owner");
  const openSettings = useUi((s) => s.openSettings);
  const [otlp, setOtlp] = useState(state.otlp);
  const [headers, setHeaders] = useState<[string, string][]>(Object.entries(state.otlp.headers));
  const [langsmith, setLangsmith] = useState(state.langsmith);
  const [problems, setProblems] = useState<string[] | null>(null);
  const [saved, setSaved] = useState(false);
  const [tested, setTested] = useState<{ ok: boolean; error?: string } | null>(null);
  const changed = () => (setSaved(false), setTested(null));

  const save = async () => {
    const settings: TracingSettings = {
      otlp: { ...otlp, headers: Object.fromEntries(headers.filter(([k]) => k.trim()).map(([k, v]) => [k.trim(), v])) },
      langsmith,
    };
    try {
      await api.saveTracing(settings);
      setProblems(null);
      setSaved(true);
    } catch (err) {
      const found = (err as { problems?: string[] }).problems;
      setProblems(found?.length ? found : [message(err)]);
    }
  };

  return (
    <Locked locked={!owner} className="space-y-4">
      <div className="space-y-2 rounded-lg border border-border p-3" data-testid="tracing-otlp">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-semibold">OpenTelemetry</p>
          <Switch checked={otlp.enabled} onCheckedChange={(v) => (setOtlp({ ...otlp, enabled: v }), changed())} label="Send runs to an OpenTelemetry collector" />
        </div>
        <p className="text-xs text-muted">
          Each run is sent as a trace over OTLP/HTTP: a span for the run, one per step, tool call and model call (with the GenAI attributes). Works with Jaeger,
          Grafana Tempo, Honeycomb, Datadog and any OpenTelemetry collector.
        </p>
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="space-y-1 text-xs">
            <span className="font-medium">Collector address</span>
            <Input className="h-8 font-mono text-xs" placeholder="http://collector:4318" value={otlp.endpoint} onChange={(e) => (setOtlp({ ...otlp, endpoint: e.target.value }), changed())} />
          </label>
          <label className="space-y-1 text-xs">
            <span className="font-medium">Service name</span>
            <Input className="h-8 text-xs" value={otlp.service_name} onChange={(e) => (setOtlp({ ...otlp, service_name: e.target.value }), changed())} />
          </label>
        </div>
        <div className="space-y-1">
          <p className="text-xs font-medium">Headers</p>
          <p className="text-xs text-faint">Sent with each export, such as an API key. A value can be {"{secret:NAME}"} to read it from Secrets.</p>
          {headers.map(([name, value], i) => (
            <div key={i} className="flex items-center gap-1.5">
              <Input aria-label={`Header name ${i + 1}`} className="h-8 font-mono text-xs" placeholder="x-api-key" value={name} onChange={(e) => (setHeaders(headers.map((h, j) => (j === i ? [e.target.value, h[1]] : h))), changed())} />
              <Input aria-label={`Header value ${i + 1}`} className="h-8 font-mono text-xs" placeholder="{secret:COLLECTOR_KEY}" value={value} onChange={(e) => (setHeaders(headers.map((h, j) => (j === i ? [h[0], e.target.value] : h))), changed())} />
              <Button size="icon-sm" variant="ghost" aria-label={`Remove header ${i + 1}`} onClick={() => (setHeaders(headers.filter((_, j) => j !== i)), changed())}>
                <Trash2 size={12} />
              </Button>
            </div>
          ))}
          <Button size="sm" variant="ghost" onClick={() => setHeaders([...headers, ["", ""]])}>
            <Plus size={12} /> Add a header
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" disabled={!saved && !state.otlp.endpoint} onClick={() => void api.testTracing().then(setTested, (err) => setTested({ ok: false, error: message(err) }))}>
            Send a test span
          </Button>
          {tested && (
            <span className={tested.ok ? "text-xs text-ok" : "text-xs text-danger"} role="status">
              {tested.ok ? "The collector took it." : tested.error}
            </span>
          )}
        </div>
      </div>

      <div className="space-y-2 rounded-lg border border-border p-3" data-testid="tracing-langsmith">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-semibold">LangSmith</p>
          <Switch checked={langsmith.enabled} onCheckedChange={(v) => (setLangsmith({ ...langsmith, enabled: v }), changed())} label="Trace runs in LangSmith" />
        </div>
        <p className="text-xs text-muted">LangChain's own tracing, with every step, model call and tool call, in a LangSmith project.</p>
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="space-y-1 text-xs">
            <span className="font-medium">Project</span>
            <Input className="h-8 text-xs" value={langsmith.project} onChange={(e) => (setLangsmith({ ...langsmith, project: e.target.value }), changed())} />
          </label>
          <label className="space-y-1 text-xs">
            <span className="font-medium">Address</span>
            <Input className="h-8 font-mono text-xs" value={langsmith.endpoint} onChange={(e) => (setLangsmith({ ...langsmith, endpoint: e.target.value }), changed())} />
          </label>
        </div>
        <p className="text-xs" data-testid="langsmith-key">
          {state.langsmith_key ? (
            <span className="text-muted">Uses the workspace's secret LANGSMITH_API_KEY.</span>
          ) : (
            <span className="text-warn">
              Add your LangSmith API key as the secret LANGSMITH_API_KEY first{" "}
              <button type="button" className="underline" onClick={() => openSettings({ secret: "LANGSMITH_API_KEY" })}>
                in Keys and providers
              </button>
              .
            </span>
          )}
        </p>
      </div>

      <Button size="sm" onClick={() => void save()}>
        {saved ? "Saved" : "Save tracing"}
      </Button>
      {problems && (
        <ul className="list-disc pl-4 text-xs text-danger" role="alert">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
    </Locked>
  );
}

/** Settings → Tracing: send runs to an OpenTelemetry collector and to LangSmith. */
export function TracingSection() {
  const [state, setState] = useState<TracingState | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api
      .tracing()
      .then(setState)
      .catch((err) => setError(message(err)));
  }, []);
  return (
    <section className="space-y-3" aria-label="Tracing">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold">
        <Activity size={14} /> Tracing
      </h3>
      <p className="text-xs text-muted">
        Send this workspace's runs to your tracing tools. Traces leave after each part of a run ends, so they never slow it down. The Dashboard (on the home page) shows
        runs, errors, time and cost over days.
      </p>
      {error && <p className="text-xs text-danger">{error}</p>}
      {state && <TracingForm state={state} />}
    </section>
  );
}
