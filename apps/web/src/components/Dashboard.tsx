import { ArrowLeft, BarChart3, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Dashboard as DashboardData, DashboardDay, FlowListItem } from "../lib/types";
import { formatMs as formatDuration, formatTokens } from "../lib/utils";
import { ColumnChart, LineChart, compact } from "./charts";
import { money } from "./dialogs/Gateway";
import { InboxLink } from "./TopBar";
import { Button, Select } from "./ui";

const PERIODS: [number, string][] = [
  [7, "Last 7 days"],
  [30, "Last 30 days"],
  [90, "Last 90 days"],
];
const ENVIRONMENTS: [string, string][] = [
  ["", "Every environment"],
  ["drafts", "Drafts"],
  ["dev", "Development"],
  ["staging", "Staging"],
  ["production", "Production"],
];

function dayLabel(day: DashboardDay) {
  return new Date(`${day.day}T00:00:00Z`).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

function percent(share: number | null) {
  return share == null ? "—" : `${(share * 100).toFixed(share > 0 && share < 0.1 ? 1 : 0)}%`;
}

function Tile({ label, value, note, testId }: { label: string; value: string; note?: string; testId: string }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-3 shadow-sm" data-testid={testId}>
      <p className="text-xs text-muted">{label}</p>
      <p className="text-2xl font-semibold">{value}</p>
      {note && <p className="text-xs text-faint">{note}</p>}
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2 rounded-xl border border-border bg-surface p-4 shadow-sm" aria-label={title}>
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

/** Runs, errors, latency, tokens and cost over time, for the workspace, a flow or an environment. */
export function Dashboard() {
  const [days, setDays] = useState(30);
  const [flowId, setFlowId] = useState("");
  const [environment, setEnvironment] = useState("");
  const [flows, setFlows] = useState<FlowListItem[]>([]);
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.flows().then(setFlows).catch(() => setFlows([]));
  }, []);
  useEffect(() => {
    setLoading(true);
    api
      .dashboard(days, flowId || undefined, environment || undefined)
      .then((d) => (setData(d), setError(null)))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [days, flowId, environment]);

  const labels = data?.series.map(dayLabel) ?? [];
  const t = data?.totals;
  return (
    <div className="h-full overflow-y-auto">
      <header className="sticky top-0 z-10 border-b border-border bg-surface/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-6">
          <Button variant="ghost" size="icon-sm" aria-label="Back" onClick={() => (window.location.hash = "#/")}>
            <ArrowLeft size={16} />
          </Button>
          <BarChart3 size={18} className="text-accent" />
          <h1 className="text-base font-semibold">Dashboard</h1>
          <div className="ml-auto flex items-center gap-1">
            <InboxLink />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl space-y-5 px-6 py-6">
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="What the dashboard shows">
          <Select aria-label="Period" className="h-8 w-40 text-xs" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {PERIODS.map(([d, label]) => (
              <option key={d} value={d}>
                {label}
              </option>
            ))}
          </Select>
          <Select aria-label="Flow" className="h-8 w-56 text-xs" value={flowId} onChange={(e) => setFlowId(e.target.value)}>
            <option value="">Every flow</option>
            {flows.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </Select>
          <Select aria-label="Environment" className="h-8 w-44 text-xs" value={environment} onChange={(e) => setEnvironment(e.target.value)}>
            {ENVIRONMENTS.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </Select>
          {loading && <Loader2 size={14} className="animate-spin text-muted" />}
          <span className="text-xs text-faint">Days are in UTC.</span>
        </div>
        {error && <p className="text-sm text-danger">{error}</p>}
        {data && t && (
          <div className={loading ? "space-y-5 opacity-60 transition-opacity" : "space-y-5 transition-opacity"}>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
              <Tile testId="tile-runs" label="Runs" value={t.runs.toLocaleString()} note={t.paused ? `${t.paused} waiting for someone` : undefined} />
              <Tile testId="tile-errors" label="Failed" value={percent(t.error_rate)} note={`${t.errors} of ${t.ok + t.errors} finished`} />
              <Tile testId="tile-latency" label="Time to finish (p95)" value={t.p95_ms == null ? "—" : formatDuration(t.p95_ms)} note={t.p50_ms == null ? undefined : `half finish within ${formatDuration(t.p50_ms)}`} />
              <Tile testId="tile-tokens" label="Tokens" value={compact(t.input_tokens + t.output_tokens)} note={`${formatTokens(t.input_tokens)} in, ${formatTokens(t.output_tokens)} out`} />
              <Tile testId="tile-cost" label="Cost" value={money(t.cost)} note="from the model gateway" />
            </div>
            <div className="grid gap-4 lg:grid-cols-2">
              <Card title="Runs a day">
                <ColumnChart
                  title="Runs a day"
                  integer
                  labels={labels}
                  format={(n) => n.toLocaleString()}
                  series={[
                    { key: "other", label: "Succeeded or waiting", color: "var(--chart-quiet)", values: data.series.map((d) => d.runs - d.errors) },
                    { key: "errors", label: "Failed", color: "var(--chart-critical)", values: data.series.map((d) => d.errors) },
                  ]}
                />
              </Card>
              <Card title="Time to finish">
                <LineChart
                  title="Time to finish, per day"
                  labels={labels}
                  format={formatDuration}
                  tickFormat={(n) => (n === 0 ? "0" : formatDuration(n))}
                  series={[
                    { key: "p50", label: "p50", color: "var(--chart-1)", values: data.series.map((d) => d.p50_ms) },
                    { key: "p95", label: "p95", color: "var(--chart-2)", values: data.series.map((d) => d.p95_ms) },
                  ]}
                />
              </Card>
              <Card title="Tokens a day">
                <ColumnChart title="Tokens a day" integer labels={labels} format={(n) => n.toLocaleString()} series={[{ key: "tokens", label: "Tokens", color: "var(--chart-1)", values: data.series.map((d) => d.input_tokens + d.output_tokens) }]} />
              </Card>
              <Card title="Cost a day">
                <ColumnChart title="Cost a day" labels={labels} format={money} tickFormat={money} series={[{ key: "cost", label: "Cost", color: "var(--chart-1)", values: data.series.map((d) => d.cost) }]} />
              </Card>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card title="Flows">
                {data.flows.length === 0 ? (
                  <p className="text-xs text-faint">No runs in this period.</p>
                ) : (
                  <table className="w-full text-xs" data-testid="dashboard-flows">
                    <thead className="text-left text-faint">
                      <tr>
                        <th className="py-1 font-medium">Flow</th>
                        <th className="py-1 text-right font-medium">Runs</th>
                        <th className="py-1 text-right font-medium">Failed</th>
                        <th className="py-1 text-right font-medium">p95</th>
                        <th className="py-1 text-right font-medium">Cost</th>
                      </tr>
                    </thead>
                    <tbody className="tabular-nums">
                      {data.flows.map((f) => (
                        <tr key={f.flow_id ?? f.flow} className="border-t border-border">
                          <td className="py-1">{f.flow_id ? <a className="hover:underline" href={`#/flows/${f.flow_id}`}>{f.flow}</a> : f.flow}</td>
                          <td className="py-1 text-right">{f.runs}</td>
                          <td className="py-1 text-right">{percent(f.error_rate)}</td>
                          <td className="py-1 text-right">{f.p95_ms == null ? "—" : formatDuration(f.p95_ms)}</td>
                          <td className="py-1 text-right">{money(f.cost)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </Card>
              <Card title="Common errors">
                {data.errors.length === 0 ? (
                  <p className="text-xs text-faint">No failed runs in this period.</p>
                ) : (
                  <ul className="space-y-2 text-xs" data-testid="dashboard-errors">
                    {data.errors.map((e) => (
                      <li key={`${e.flow}:${e.kind}:${e.message}`} className="space-y-0.5">
                        <p>
                          <span className="font-semibold">{e.count}×</span> {e.message}
                        </p>
                        <p className="text-faint">
                          {e.flow} · last {new Date(e.last_at * 1000).toLocaleString()}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>

            <details className="rounded-xl border border-border bg-surface p-4 shadow-sm">
              <summary className="cursor-pointer text-sm font-semibold">Each day, as a table</summary>
              <table className="mt-2 w-full text-xs" data-testid="dashboard-days">
                <thead className="text-left text-faint">
                  <tr>
                    <th className="py-1 font-medium">Day (UTC)</th>
                    <th className="py-1 text-right font-medium">Runs</th>
                    <th className="py-1 text-right font-medium">Failed</th>
                    <th className="py-1 text-right font-medium">p50</th>
                    <th className="py-1 text-right font-medium">p95</th>
                    <th className="py-1 text-right font-medium">Tokens</th>
                    <th className="py-1 text-right font-medium">Cost</th>
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {[...data.series].reverse().map((d) => (
                    <tr key={d.day} className="border-t border-border">
                      <td className="py-1">{d.day}</td>
                      <td className="py-1 text-right">{d.runs}</td>
                      <td className="py-1 text-right">{d.errors}</td>
                      <td className="py-1 text-right">{d.p50_ms == null ? "—" : formatDuration(d.p50_ms)}</td>
                      <td className="py-1 text-right">{d.p95_ms == null ? "—" : formatDuration(d.p95_ms)}</td>
                      <td className="py-1 text-right">{(d.input_tokens + d.output_tokens).toLocaleString()}</td>
                      <td className="py-1 text-right">{money(d.cost)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </div>
        )}
      </main>
    </div>
  );
}

export function DashboardLink() {
  return (
    <a href="#/dashboard" className="flex h-8 items-center gap-1 rounded-md px-2 text-sm text-muted hover:bg-surface-2 hover:text-text" data-testid="dashboard-link">
      <BarChart3 size={15} />
      <span className="hidden md:inline">Dashboard</span>
    </a>
  );
}
