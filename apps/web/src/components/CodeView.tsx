import { lazy, Suspense } from "react";

const MonacoEditor = lazy(() => import("./MonacoEditor"));

export function CodeView({
  value,
  onChange,
  language = "python",
  readOnly = false,
  height = 320,
  label,
}: {
  value: string;
  onChange?: (value: string) => void;
  language?: string;
  readOnly?: boolean;
  height?: number | string;
  label?: string;
}) {
  const fallback = readOnly ? (
    <pre className="overflow-auto p-3 font-mono text-xs" style={{ height }}>
      {value}
    </pre>
  ) : (
    <textarea
      aria-label={label}
      className="w-full bg-transparent p-3 font-mono text-xs outline-none"
      style={{ height }}
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  );
  return (
    <div className="overflow-hidden rounded-md border border-border bg-surface" data-testid="code-view">
      <Suspense fallback={fallback}>
        <MonacoEditor value={value} onChange={onChange} language={language} readOnly={readOnly} height={height} label={label} />
      </Suspense>
    </div>
  );
}
