import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

/** One series of a chart: its values in the same order as the chart's labels. */
export interface Series {
  key: string;
  label: string;
  /** a CSS colour, e.g. var(--chart-1) */
  color: string;
  values: (number | null)[];
}

interface ChartProps {
  /** what the chart shows, for screen readers and the keyboard readout */
  title: string;
  /** one per position along the x axis (days) */
  labels: string[];
  series: Series[];
  format: (value: number) => string;
  /** how the y axis writes its ticks (compact numbers by default) */
  tickFormat?: (value: number) => string;
  /** counts: ticks in whole steps */
  integer?: boolean;
  height?: number;
}

const MARGIN = { top: 8, right: 40, bottom: 22, left: 48 };

function trim(text: string): string {
  return text.includes(".") ? text.replace(/\.?0+$/, "") : text;
}

export function compact(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `${trim((n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1))}M`;
  if (Math.abs(n) >= 1_000) return `${trim((n / 1_000).toFixed(n >= 10_000 ? 0 : 1))}K`;
  return Number.isInteger(n) ? String(n) : trim(n.toFixed(n < 1 ? 2 : 1));
}

/** Round ticks from 0 to a little above ``max``: steps of 1, 2 or 5 × 10ⁿ. */
export function niceTicks(max: number, count = 4, integer = false): number[] {
  if (!(max > 0)) return [0, 1];
  const rough = integer ? Math.max(1, max / count) : max / count;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= rough) ?? 10 * power;
  const ticks = [];
  for (let t = 0; t <= max + step * 0.001; t += step) ticks.push(Number(t.toPrecision(12)));
  if (ticks[ticks.length - 1] < max) ticks.push(Number((ticks[ticks.length - 1] + step).toPrecision(12)));
  return ticks;
}

function useWidth(fallback = 600): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(200, Math.floor(entry.contentRect.width))));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** Which position the pointer or the arrow keys are on. */
function useActive(count: number) {
  const [active, setActive] = useState<number | null>(null);
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const from = active ?? count - 1;
      setActive(Math.max(0, Math.min(count - 1, from + (e.key === "ArrowLeft" ? -1 : 1))));
    }
  };
  return { active, setActive, onKeyDown };
}

function Legend({ series, mark }: { series: Series[]; mark: "rect" | "line" }) {
  if (series.length < 2) return null;
  return (
    <ul className="flex flex-wrap gap-3 text-xs text-muted">
      {series.map((s) => (
        <li key={s.key} className="flex items-center gap-1.5">
          <span aria-hidden className={mark === "rect" ? "h-2.5 w-2.5 rounded-sm" : "h-0.5 w-3 rounded"} style={{ background: s.color }} />
          {s.label}
        </li>
      ))}
    </ul>
  );
}

function Readout({ label, series, index, format, left }: { label: string; series: Series[]; index: number; format: (n: number) => string; left: number }) {
  return (
    <div
      role="status"
      className="pointer-events-none absolute top-1 z-10 min-w-28 -translate-x-1/2 rounded-md border border-border bg-surface px-2 py-1.5 text-xs shadow-md"
      style={{ left }}
    >
      <p className="mb-0.5 text-faint">{label}</p>
      {series.map((s) => (
        <p key={s.key} className="flex items-center gap-1.5 whitespace-nowrap">
          <span aria-hidden className="h-0.5 w-3 rounded" style={{ background: s.color }} />
          <span className="font-semibold text-text">{s.values[index] == null ? "—" : format(s.values[index] as number)}</span>
          {series.length > 1 && <span className="text-muted">{s.label}</span>}
        </p>
      ))}
    </div>
  );
}

function Axes({ ticks, y, width, plotBottom, labels, x, tickFormat }: { ticks: number[]; y: (v: number) => number; width: number; plotBottom: number; labels: string[]; x: (i: number) => number; tickFormat: (n: number) => string }) {
  const shown = labels.length <= 1 ? [0] : [0, Math.floor((labels.length - 1) / 2), labels.length - 1];
  return (
    <g className="text-faint" fontSize={10}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={MARGIN.left} x2={width - MARGIN.right} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth={1} />
          <text x={MARGIN.left - 6} y={y(t)} dy="0.32em" textAnchor="end" fill="currentColor" style={{ fontVariantNumeric: "tabular-nums" }}>
            {tickFormat(t)}
          </text>
        </g>
      ))}
      {[...new Set(shown)].map((i) => (
        <text key={i} x={x(i)} y={plotBottom + 15} textAnchor={i === 0 ? "start" : i === labels.length - 1 ? "end" : "middle"} fill="currentColor">
          {labels[i]}
        </text>
      ))}
    </g>
  );
}

/** Columns per position, stacked bottom to top in series order (the first series at the base). */
export function ColumnChart({ title, labels, series, format, tickFormat = compact, integer = false, height = 168 }: ChartProps) {
  const [ref, width] = useWidth();
  const { active, setActive, onKeyDown } = useActive(labels.length);
  const n = labels.length;
  const plotW = width - MARGIN.left - MARGIN.right;
  const plotBottom = height - MARGIN.bottom;
  const plotH = plotBottom - MARGIN.top;
  const totals = labels.map((_, i) => series.reduce((sum, s) => sum + (s.values[i] ?? 0), 0));
  const ticks = niceTicks(Math.max(...totals, 0), 4, integer);
  const top = ticks[ticks.length - 1] || 1;
  const y = (v: number) => plotBottom - (v / top) * plotH;
  const band = plotW / Math.max(n, 1);
  const barW = Math.max(2, Math.min(24, band - 2));
  const x = (i: number) => MARGIN.left + band * i + band / 2;
  const move = (e: PointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    setActive(Math.max(0, Math.min(n - 1, Math.floor(((e.clientX - box.left) / box.width) * n))));
  };
  return (
    <figure className="space-y-2">
      <Legend series={series} mark="rect" />
      <div ref={ref} className="relative outline-none focus-visible:ring-2 focus-visible:ring-ring" tabIndex={0} aria-label={`${title}: use the arrow keys to read each day`} onKeyDown={onKeyDown} onBlur={() => setActive(null)}>
        <svg width={width} height={height} role="img" aria-label={title}>
          <Axes ticks={ticks} y={y} width={width} plotBottom={plotBottom} labels={labels} x={x} tickFormat={tickFormat} />
          {active != null && <rect x={MARGIN.left + band * active} y={MARGIN.top} width={band} height={plotH} fill="var(--surface-2)" />}
          {labels.map((_, i) => {
            let base = 0;
            const parts = series
              .map((s) => ({ s, v: s.values[i] ?? 0 }))
              .filter((p) => p.v > 0)
              .map((p, k, all) => {
                const y0 = y(base);
                base += p.v;
                const y1 = y(base);
                // A 2px gap of surface between stacked parts; only the top part is rounded.
                const bottom = k === 0 ? y0 : y0 - 2;
                const h = Math.max(1, bottom - y1);
                const r = k === all.length - 1 ? Math.min(4, barW / 2, h) : 0;
                const left = x(i) - barW / 2;
                const d = `M${left},${bottom} V${y1 + r} Q${left},${y1} ${left + r},${y1} H${left + barW - r} Q${left + barW},${y1} ${left + barW},${y1 + r} V${bottom} Z`;
                return <path key={p.s.key} d={d} fill={p.s.color} />;
              });
            return <g key={i}>{parts}</g>;
          })}
          <line x1={MARGIN.left} x2={width - MARGIN.right} y1={plotBottom} y2={plotBottom} stroke="var(--muted)" strokeOpacity={0.4} strokeWidth={1} />
          <rect x={MARGIN.left} y={MARGIN.top} width={plotW} height={plotH} fill="transparent" onPointerMove={move} onPointerLeave={() => setActive(null)} />
        </svg>
        {active != null && <Readout label={labels[active]} series={series} index={active} format={format} left={Math.min(Math.max(x(active), 70), width - 70)} />}
      </div>
    </figure>
  );
}

/** Lines across positions, with a crosshair readout of every series. */
export function LineChart({ title, labels, series, format, tickFormat = compact, integer = false, height = 168 }: ChartProps) {
  const [ref, width] = useWidth();
  const { active, setActive, onKeyDown } = useActive(labels.length);
  const n = labels.length;
  const plotW = width - MARGIN.left - MARGIN.right;
  const plotBottom = height - MARGIN.bottom;
  const plotH = plotBottom - MARGIN.top;
  const all = series.flatMap((s) => s.values.filter((v): v is number => v != null));
  const ticks = niceTicks(Math.max(...all, 0), 4, integer);
  const top = ticks[ticks.length - 1] || 1;
  const y = (v: number) => plotBottom - (v / top) * plotH;
  const band = plotW / Math.max(n, 1);
  const x = (i: number) => MARGIN.left + band * i + band / 2;
  const move = (e: PointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    setActive(Math.max(0, Math.min(n - 1, Math.floor(((e.clientX - box.left) / box.width) * n))));
  };
  const ends = series.map((s) => {
    const i = s.values.map((v, k) => (v == null ? -1 : k)).filter((k) => k >= 0).pop();
    return i == null ? null : { i, y: y(s.values[i] as number) };
  });
  // End labels only where they don't run into each other; the legend and readout cover the rest.
  const clear = ends.every((a, k) => !a || ends.every((b, j) => j === k || !b || Math.abs(a.y - b.y) >= 12));
  return (
    <figure className="space-y-2">
      <Legend series={series} mark="line" />
      <div ref={ref} className="relative outline-none focus-visible:ring-2 focus-visible:ring-ring" tabIndex={0} aria-label={`${title}: use the arrow keys to read each day`} onKeyDown={onKeyDown} onBlur={() => setActive(null)}>
        <svg width={width} height={height} role="img" aria-label={title}>
          <Axes ticks={ticks} y={y} width={width} plotBottom={plotBottom} labels={labels} x={x} tickFormat={tickFormat} />
          {series.map((s) => {
            // A day with no value breaks the line; a lone value is a dot.
            const runs: [number, number][][] = [[]];
            s.values.forEach((v, i) => {
              if (v == null) runs.push([]);
              else runs[runs.length - 1].push([x(i), y(v)]);
            });
            return runs
              .filter((run) => run.length > 0)
              .map((run, k) =>
                run.length > 1 ? (
                  <path key={`${s.key}${k}`} d={run.map(([px, py], m) => `${m ? "L" : "M"}${px},${py}`).join(" ")} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                ) : (
                  <circle key={`${s.key}${k}`} cx={run[0][0]} cy={run[0][1]} r={3} fill={s.color} />
                ),
              );
          })}
          {series.map((s, k) => {
            const end = ends[k];
            if (!end) return null;
            return (
              <g key={s.key}>
                <circle cx={x(end.i)} cy={end.y} r={4} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
                {clear && series.length > 1 && (
                  <text x={x(end.i) + 8} y={end.y} dy="0.32em" fontSize={10} fill="var(--muted)">
                    {s.label}
                  </text>
                )}
              </g>
            );
          })}
          {active != null && (
            <g>
              <line x1={x(active)} x2={x(active)} y1={MARGIN.top} y2={plotBottom} stroke="var(--muted)" strokeOpacity={0.5} strokeWidth={1} />
              {series.map((s) =>
                s.values[active] == null ? null : <circle key={s.key} cx={x(active)} cy={y(s.values[active] as number)} r={4} fill={s.color} stroke="var(--surface)" strokeWidth={2} />,
              )}
            </g>
          )}
          <line x1={MARGIN.left} x2={width - MARGIN.right} y1={plotBottom} y2={plotBottom} stroke="var(--muted)" strokeOpacity={0.4} strokeWidth={1} />
          <rect x={MARGIN.left} y={MARGIN.top} width={plotW} height={plotH} fill="transparent" onPointerMove={move} onPointerLeave={() => setActive(null)} />
        </svg>
        {active != null && <Readout label={labels[active]} series={series} index={active} format={format} left={Math.min(Math.max(x(active), 70), width - 70)} />}
      </div>
    </figure>
  );
}
