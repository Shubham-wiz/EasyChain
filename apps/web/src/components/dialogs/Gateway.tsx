import { Gauge, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { Budget, BudgetScope, GatewaySettings, GatewayState, Spending, SpendingBy, SpendingPeriod } from "../../lib/types";
import { formatCost, formatTokens } from "../../lib/utils";
import { useCan } from "../../state/auth";
import { Button, Input, Locked, Select } from "../ui";

const PERIODS: [SpendingPeriod, string][] = [
  ["today", "Today"],
  ["7d", "Last 7 days"],
  ["30d", "Last 30 days"],
  ["month", "This month"],
];
const GROUPS: [SpendingBy, string][] = [
  ["flow", "Flow"],
  ["person", "Person"],
  ["environment", "Environment"],
  ["model", "Model"],
  ["run", "Run"],
];
const CACHE_SCOPES: [Exclude<BudgetScope, "workspace">, string][] = [
  ["drafts", "Drafts"],
  ["dev", "Development"],
  ["staging", "Staging"],
  ["production", "Production"],
];

function message(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

export function money(amount: number): string {
  return amount === 0 ? "$0.00" : amount < 0.01 ? formatCost(amount) : `$${amount.toFixed(2)}`;
}

/** What the workspace's model calls cost, added up per flow, person, environment, model or run. */
export function SpendingTable() {
  const [period, setPeriod] = useState<SpendingPeriod>("month");
  const [by, setBy] = useState<SpendingBy>("flow");
  const [found, setFound] = useState<Spending | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api
      .spending(period, by)
      .then((s) => (setFound(s), setError(null)))
      .catch((err) => setError(message(err)));
  }, [period, by]);
  const total = found?.total;
  return (
    <div className="space-y-2" data-testid="spending">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="Spending period" className="h-8 w-36 text-xs" value={period} onChange={(e) => setPeriod(e.target.value as SpendingPeriod)}>
          {PERIODS.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </Select>
        <Select aria-label="Spending by" className="h-8 w-36 text-xs" value={by} onChange={(e) => setBy(e.target.value as SpendingBy)}>
          {GROUPS.map(([id, label]) => (
            <option key={id} value={id}>
              per {label.toLowerCase()}
            </option>
          ))}
        </Select>
        {total && (
          <p className="text-xs text-muted" data-testid="spending-total">
            <span className="font-semibold text-text">{money(total.cost)}</span> · {total.calls} model call{total.calls === 1 ? "" : "s"}
            {total.cached > 0 && `, ${total.cached} from the cache`}
            {total.errors > 0 && `, ${total.errors} failed`}
          </p>
        )}
      </div>
      {error && <p className="text-xs text-danger">{error}</p>}
      {found && found.rows.length === 0 && <p className="text-xs text-faint">No model calls in this period.</p>}
      {found && found.rows.length > 0 && (
        <table className="w-full text-xs">
          <thead className="text-left text-faint">
            <tr>
              <th className="py-1 font-medium">{GROUPS.find(([id]) => id === by)?.[1]}</th>
              <th className="py-1 text-right font-medium">Calls</th>
              <th className="py-1 text-right font-medium">Tokens in / out</th>
              <th className="py-1 text-right font-medium">Cost</th>
            </tr>
          </thead>
          <tbody>
            {found.rows.map((row) => (
              <tr key={`${row.key}:${row.label}`} className="border-t border-border">
                <td className="py-1">
                  {row.label}
                  {by === "run" && row.by && <span className="text-faint"> · {row.by}</span>}
                </td>
                <td className="py-1 text-right tabular-nums">{row.calls}</td>
                <td className="py-1 text-right tabular-nums">
                  {formatTokens(row.input_tokens)} / {formatTokens(row.output_tokens)}
                </td>
                <td className="py-1 text-right tabular-nums">
                  {money(row.cost)}
                  {row.unpriced > 0 && <span title={`${row.unpriced} call(s) with no price: add one under Prices`}> +?</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {total && total.unpriced > 0 && (
        <p className="text-xs text-muted">
          {total.unpriced} call{total.unpriced === 1 ? " has" : "s have"} no price (a model Easy Chain doesn't know): add one under Prices, below.
        </p>
      )}
    </div>
  );
}

type BudgetDraft = { limit: string; period: Budget["period"]; alert: string; stop: boolean };

function draftOf(budget: Budget | null): BudgetDraft {
  return budget
    ? { limit: String(budget.limit), period: budget.period, alert: String(Math.round(budget.alert_at * 100)), stop: budget.stop }
    : { limit: "", period: "month", alert: "80", stop: true };
}

type Rows = [string, string][];

function PairRows({ label, from, to, rows, onChange, add }: { label: string; from: string; to: string; rows: Rows; onChange: (rows: Rows) => void; add: string }) {
  return (
    <div className="space-y-1">
      {rows.map(([a, b], i) => (
        <div key={i} className="flex items-center gap-1.5">
          <Input aria-label={`${label}: ${from} ${i + 1}`} className="h-8 font-mono text-xs" placeholder={from} value={a} onChange={(e) => onChange(rows.map((r, j) => (j === i ? [e.target.value, r[1]] : r)))} />
          <span aria-hidden className="text-muted">
            →
          </span>
          <Input aria-label={`${label}: ${to} ${i + 1}`} className="h-8 font-mono text-xs" placeholder={to} value={b} onChange={(e) => onChange(rows.map((r, j) => (j === i ? [r[0], e.target.value] : r)))} />
          <Button size="icon-sm" variant="ghost" aria-label={`Remove ${label.toLowerCase()} ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            <Trash2 size={12} />
          </Button>
        </div>
      ))}
      <Button size="sm" variant="ghost" onClick={() => onChange([...rows, ["", ""]])}>
        <Plus size={12} /> {add}
      </Button>
    </div>
  );
}

function GatewayForm({ state, reload }: { state: GatewayState; reload: (next?: GatewayState) => void }) {
  const owner = useCan("owner");
  const s = state.settings;
  const [budgets, setBudgets] = useState<Record<string, BudgetDraft>>(() => Object.fromEntries(state.budgets.map((b) => [b.scope, draftOf(b.budget)])));
  const [caching, setCaching] = useState<string[]>(s.cache.scopes);
  const [hours, setHours] = useState(String(s.cache.hours));
  const [fallbacks, setFallbacks] = useState<Rows>(Object.entries(s.fallbacks).map(([m, others]) => [m, others.join(", ")]));
  const [retries, setRetries] = useState(s.retries == null ? "" : String(s.retries));
  const [rates, setRates] = useState<Rows>(Object.entries(s.per_second).map(([p, r]) => [p, String(r)]));
  const [prices, setPrices] = useState<Rows>(Object.entries(s.prices).map(([m, p]) => [m, `${p.input} / ${p.output}`]));
  const [problems, setProblems] = useState<string[] | null>(null);
  const [saved, setSaved] = useState(false);
  const [forgotten, setForgotten] = useState<number | null>(null);
  const changed = () => setSaved(false);

  const build = (): GatewaySettings | string[] => {
    const wrong: string[] = [];
    const out: GatewaySettings = { budgets: {}, cache: { scopes: caching as GatewaySettings["cache"]["scopes"], hours: Number(hours) || 24 }, fallbacks: {}, retries: retries.trim() ? Number(retries) : null, per_second: {}, prices: {} };
    for (const status of state.budgets) {
      const d = budgets[status.scope];
      if (!d?.limit.trim()) continue;
      out.budgets[status.scope] = { limit: Number(d.limit), period: d.period, alert_at: Math.min(Number(d.alert || 0), 99) / 100, stop: d.stop };
    }
    for (const [model, others] of fallbacks) {
      if (model.trim()) out.fallbacks[model.trim()] = others.split(",").map((o) => o.trim()).filter(Boolean);
    }
    for (const [provider, rate] of rates) {
      if (provider.trim()) out.per_second[provider.trim()] = Number(rate);
    }
    for (const [model, price] of prices) {
      if (!model.trim()) continue;
      const [input, output] = price.split("/").map((p) => Number(p.trim()));
      if (Number.isNaN(input) || Number.isNaN(output) || output === undefined) wrong.push(`Give ${model}'s price as input / output, in dollars per million tokens.`);
      else out.prices[model.trim()] = { input, output };
    }
    return wrong.length ? wrong : out;
  };

  const save = async () => {
    const settings = build();
    if (Array.isArray(settings)) return setProblems(settings);
    try {
      const next = await api.saveGateway(settings);
      setProblems(null);
      setSaved(true);
      reload(next);
    } catch (err) {
      const found = (err as { problems?: string[] }).problems;
      setProblems(found?.length ? found : [message(err)]);
    }
  };

  return (
    <Locked locked={!owner} className="space-y-4">
      <div className="space-y-2">
        <p className="text-xs font-medium">Budgets</p>
        <ul className="space-y-2">
          {state.budgets.map((status) => {
            const d = budgets[status.scope];
            const set = (patch: Partial<BudgetDraft>) => (setBudgets({ ...budgets, [status.scope]: { ...d, ...patch } }), changed());
            const share = status.share ?? 0;
            return (
              <li key={status.scope} className="space-y-1 rounded-md border border-border p-2" data-testid={`budget-${status.scope}`}>
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="font-medium">{status.label}</span>
                  <span className="text-muted" data-testid={`budget-${status.scope}-spent`}>
                    {money(status.spent)}
                    {status.budget ? ` of ${money(status.budget.limit)} ${status.budget.period === "day" ? "today" : "this month"}` : ` this month`}
                  </span>
                </div>
                {status.budget && (
                  <div className="h-1.5 overflow-hidden rounded bg-border" role="meter" aria-label={`${status.label} budget used`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(share * 100)}>
                    <div className={share >= 1 ? "h-full bg-danger" : share >= status.budget.alert_at ? "h-full bg-warn" : "h-full bg-accent"} style={{ width: `${Math.min(share, 1) * 100}%` }} />
                  </div>
                )}
                <div className="flex flex-wrap items-center gap-1.5 text-xs">
                  <span className="text-muted">$</span>
                  <Input aria-label={`${status.label} budget in dollars`} className="h-7 w-20 text-xs" inputMode="decimal" placeholder="no limit" value={d.limit} onChange={(e) => set({ limit: e.target.value })} />
                  <Select aria-label={`${status.label} budget period`} className="h-7 w-24 text-xs" value={d.period} onChange={(e) => set({ period: e.target.value as Budget["period"] })}>
                    <option value="month">a month</option>
                    <option value="day">a day</option>
                  </Select>
                  <span className="text-muted">alert at</span>
                  <Input aria-label={`${status.label} alert at percent`} className="h-7 w-14 text-xs" inputMode="numeric" value={d.alert} onChange={(e) => set({ alert: e.target.value })} />
                  <span className="text-muted">%</span>
                  <label className="flex items-center gap-1">
                    <input type="checkbox" checked={d.stop} onChange={(e) => set({ stop: e.target.checked })} aria-label={`Stop ${status.label} model calls when the budget is used up`} />
                    stop model calls when it's used up
                  </label>
                </div>
              </li>
            );
          })}
        </ul>
        <p className="text-xs text-faint">
          Workspace covers every run; Drafts covers flows that aren't published, the Tests tab and the copilot. Alerts go to the channels in Notifications. Days and months are in UTC.
        </p>
      </div>

      <div className="space-y-1">
        <p className="text-xs font-medium">Cache answers</p>
        <p className="text-xs text-faint">The same model, settings and messages get the answer kept the first time, for free. Good for drafts and tests; real conversations rarely repeat.</p>
        <div className="flex flex-wrap items-center gap-3 text-xs">
          {CACHE_SCOPES.map(([id, label]) => (
            <label key={id} className="flex items-center gap-1">
              <input type="checkbox" checked={caching.includes(id)} onChange={(e) => (setCaching(e.target.checked ? [...caching, id] : caching.filter((c) => c !== id)), changed())} />
              {label}
            </label>
          ))}
          <span className="text-muted">for</span>
          <Input aria-label="Keep cached answers for hours" className="h-7 w-16 text-xs" inputMode="decimal" value={hours} onChange={(e) => (setHours(e.target.value), changed())} />
          <span className="text-muted">hours</span>
          <Button size="sm" variant="ghost" onClick={() => void api.forgetAnswers().then((r) => setForgotten(r.forgotten))}>
            Forget cached answers
          </Button>
          {forgotten != null && <span className="text-faint">{forgotten} forgotten.</span>}
        </div>
      </div>

      <div className="space-y-1">
        <p className="text-xs font-medium">Fallbacks</p>
        <p className="text-xs text-faint">When a model's provider fails or can't be reached, try these instead, in order (provider:model, separated by commas). A fallback with no key here is skipped.</p>
        <PairRows label="Fallbacks" from="openai:gpt-4.1" to="anthropic:claude-sonnet-4-5, …" rows={fallbacks} onChange={(r) => (setFallbacks(r), changed())} add="Add a fallback" />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <p className="text-xs font-medium">Retries</p>
          <p className="text-xs text-faint">How many times a failed call is tried again, waiting longer each time. Blank: the provider's default.</p>
          <Input aria-label="Retries" className="h-8 w-20 text-xs" inputMode="numeric" placeholder="default" value={retries} onChange={(e) => (setRetries(e.target.value), changed())} />
        </div>
        <div className="space-y-1">
          <p className="text-xs font-medium">Smoothing</p>
          <p className="text-xs text-faint">At most this many requests a second to a provider, per server process: bursts wait instead of hitting its rate limit.</p>
          <PairRows label="Smoothing" from="openai" to="requests a second" rows={rates} onChange={(r) => (setRates(r), changed())} add="Add a provider" />
        </div>
      </div>

      <div className="space-y-1">
        <p className="text-xs font-medium">Prices</p>
        <p className="text-xs text-faint">Dollars per million tokens, input / output, for models Easy Chain has no price for (a fine-tuned one, say) or a price you pay instead.</p>
        <PairRows label="Prices" from="openai:ft:gpt-4.1:acme" to="3 / 12" rows={prices} onChange={(r) => (setPrices(r), changed())} add="Add a price" />
      </div>

      <Button size="sm" onClick={() => void save()}>
        {saved ? "Saved" : "Save the model gateway"}
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

/** Settings → Model gateway: spending, budgets and alerts, caching, fallbacks, retries, smoothing and prices. */
export function GatewaySection() {
  const [state, setState] = useState<GatewayState | null>(null);
  const [version, setVersion] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const load = (next?: GatewayState) => {
    // After a save the form already shows what was saved; only the budgets' spending changes.
    if (next) return setState(next);
    api
      .gateway()
      .then((s) => (setState(s), setVersion((v) => v + 1)))
      .catch((err) => setError(message(err)));
  };
  useEffect(() => load(), []);
  return (
    <section className="space-y-4" aria-label="Model gateway">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold">
        <Gauge size={14} /> Model gateway
      </h3>
      <p className="text-xs text-muted">
        Every model call of a run goes through Easy Chain's gateway, which records what it cost. Set budgets that alert and stop, cache answers, fall back to other models and smooth bursts. Exported code calls the providers directly.
      </p>
      <SpendingTable />
      {error && <p className="text-xs text-danger">{error}</p>}
      {state && state.alerts.length > 0 && (
        <div className="space-y-0.5" data-testid="budget-alerts">
          <p className="text-xs font-medium">Alerts sent</p>
          <ul className="text-xs text-muted">
            {state.alerts.map((a) => (
              <li key={`${a.budget}:${a.period}:${a.level}`}>
                {state.budgets.find((b) => b.scope === a.budget)?.label ?? a.budget}: {a.level >= 100 ? "used up" : `${a.level}% used`} ({money(a.spent)} of {money(a.amount)}, {a.period})
              </li>
            ))}
          </ul>
        </div>
      )}
      {state && <GatewayForm key={version} state={state} reload={load} />}
    </section>
  );
}
