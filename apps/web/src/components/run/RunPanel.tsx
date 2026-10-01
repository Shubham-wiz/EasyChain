import { CheckCircle2, ChevronDown, ChevronRight, CircleAlert, History, Loader2, MessageSquarePlus, Play, RotateCcw, Send, Square, Wrench } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../lib/api";
import { applyFix } from "../../lib/fixes";
import type { RunError, RunSummary, Step } from "../../lib/types";
import { cn, formatCost, formatMs, formatTokens, preview, timeAgo } from "../../lib/utils";
import { useCheck } from "../../state/check";
import { useFlow } from "../../state/flow";
import { replayRun, startRun, stopRun, useRun } from "../../state/run";
import { useUi } from "../../state/ui";
import { IssueList } from "../inspector/Inspector";
import { Badge, Button, Field, Input, Switch, Textarea, Tooltip } from "../ui";

function useInputStep(): Step | undefined {
  return useFlow((s) => s.spec?.steps.find((x) => x.type === "input"));
}

function ErrorCard({ error, retry }: { error: RunError; retry: () => void }) {
  const failedStep = useRun((s) => (s.final?.step ? s.final.step : Object.entries(s.steps).find(([, v]) => v.status === "error")?.[0]));
  const stepName = useFlow((s) => s.spec?.steps.find((x) => x.id === failedStep)?.name);
  const select = useUi((s) => s.select);
  const [details, setDetails] = useState(false);
  return (
    <div className="rounded-lg border border-danger/30 bg-danger-soft p-3 text-sm" role="alert" data-testid="run-error">
      <div className="flex gap-2">
        <CircleAlert size={16} className="mt-0.5 shrink-0 text-danger" />
        <div className="min-w-0 flex-1 space-y-1">
          {failedStep && (
            <button type="button" className="text-xs font-semibold text-danger underline-offset-2 hover:underline" onClick={() => select([failedStep])}>
              {stepName || failedStep} failed
            </button>
          )}
          <p className="font-medium text-text">{error.message}</p>
          {error.hint && <p className="text-xs text-muted">{error.hint}</p>}
          {error.problems?.map((p) => (
            <p key={p.field} className="text-xs text-muted">
              • {p.message}
            </p>
          ))}
          {error.fixes.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {error.fixes.map((fix) => (
                <Button key={fix.kind} size="sm" variant={fix.kind === "add_key" ? "primary" : "outline"} onClick={() => applyFix(fix, failedStep ?? undefined, retry)}>
                  <Wrench size={12} /> {fix.label}
                </Button>
              ))}
            </div>
          )}
          {error.detail && (
            <div className="pt-1">
              <button type="button" className="text-[11px] text-muted underline" onClick={() => setDetails(!details)}>
                {details ? "Hide" : "Show"} technical details
              </button>
              {details && <pre className="mt-1 max-h-40 overflow-auto rounded bg-surface p-2 font-mono text-[11px] whitespace-pre-wrap">{error.detail}</pre>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ValueView({ value }: { value: unknown }) {
  if (typeof value === "string") return <div className="text-sm leading-relaxed whitespace-pre-wrap">{value}</div>;
  if (Array.isArray(value) && value.every((m) => m && typeof m === "object" && "role" in m)) {
    return (
      <div className="space-y-1">
        {(value as { role: string; content: string }[]).map((m, i) => (
          <p key={i} className="text-xs">
            <span className="font-semibold text-muted">{m.role}:</span> <span className="whitespace-pre-wrap">{m.content}</span>
          </p>
        ))}
      </div>
    );
  }
  return <pre className="overflow-auto font-mono text-xs whitespace-pre-wrap">{JSON.stringify(value, null, 2)}</pre>;
}

function Trace() {
  const order = useRun((s) => s.order);
  const steps = useRun((s) => s.steps);
  const spec = useFlow((s) => s.spec);
  const select = useUi((s) => s.select);
  const [open, setOpen] = useState<string | null>(null);
  if (!order.length || !spec) return null;
  return (
    <section aria-label="Run trace">
      <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-faint uppercase">Steps</h3>
      <ol className="divide-y divide-border rounded-lg border border-border">
        {order.map((id) => {
          const run = steps[id];
          const step = spec.steps.find((s) => s.id === id);
          const expanded = open === id;
          return (
            <li key={id} className="text-xs">
              <button
                type="button"
                className="flex w-full items-center gap-2 px-2.5 py-2 text-left hover:bg-surface-2"
                onClick={() => {
                  select([id]);
                  setOpen(expanded ? null : id);
                }}
                aria-expanded={expanded}
              >
                {run.status === "running" ? (
                  <Loader2 size={13} className="animate-spin text-accent" />
                ) : run.status === "error" ? (
                  <CircleAlert size={13} className="text-danger" />
                ) : (
                  <CheckCircle2 size={13} className="text-ok" />
                )}
                <span className="min-w-0 flex-1 truncate font-medium">{step?.name || id}</span>
                {run.exit && <Badge tone="accent">↳ {run.exit}</Badge>}
                {run.usage && <span className="text-faint">{formatTokens(run.usage.input_tokens + run.usage.output_tokens)} tok</span>}
                {run.cost != null && <span className="text-faint">{formatCost(run.cost)}</span>}
                <span className="w-12 text-right text-faint">{formatMs(run.durationMs)}</span>
                {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              </button>
              {expanded && (
                <div className="space-y-2 bg-surface-2/50 px-3 py-2">
                  {run.model && <p className="text-faint">Model: {run.model}</p>}
                  <div>
                    <p className="font-semibold text-muted">Read</p>
                    {Object.keys(run.input ?? {}).length ? <ValueView value={run.input} /> : <p className="text-faint">nothing</p>}
                  </div>
                  <div>
                    <p className="font-semibold text-muted">Saved</p>
                    {run.error ? <p className="text-danger">{run.error.message}</p> : Object.keys(run.output ?? {}).length ? <ValueView value={run.output} /> : <p className="text-faint">no changes</p>}
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function RunHistory({ flowId }: { flowId: string }) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [open, setOpen] = useState(false);
  const status = useRun((s) => s.status);
  useEffect(() => {
    if (open) api.runs(flowId).then(setRuns).catch(() => setRuns([]));
  }, [open, flowId, status]);
  return (
    <section>
      <button type="button" className="flex items-center gap-1.5 text-xs font-medium text-muted hover:text-text" onClick={() => setOpen(!open)} aria-expanded={open}>
        <History size={13} /> Recent runs
      </button>
      {open && (
        <ul className="mt-1.5 space-y-1">
          {!runs.length && <li className="text-xs text-faint">No runs yet.</li>}
          {runs.slice(0, 12).map((r) => (
            <li key={r.run_id} className="flex items-center gap-2 text-xs">
              {r.status === "ok" ? <CheckCircle2 size={12} className="text-ok" /> : r.status === "error" ? <CircleAlert size={12} className="text-danger" /> : <Loader2 size={12} className="animate-spin" />}
              <span className="min-w-0 flex-1 truncate text-muted">{preview(r.inputs, 50) || "(no inputs)"}</span>
              <span className="text-faint">{timeAgo(r.started)}</span>
              <Tooltip content="Play this run back on the canvas">
                <button
                  type="button"
                  className="text-accent hover:underline"
                  onClick={async () => {
                    const detail = await api.run(r.run_id);
                    void replayRun(detail.events);
                  }}
                >
                  Replay
                </button>
              </Tooltip>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function StandInToggle() {
  const standIn = useUi((s) => s.standIn);
  const setStandIn = useUi((s) => s.setStandIn);
  return (
    <label className="flex items-center gap-2 text-xs text-muted">
      <Switch checked={standIn} onCheckedChange={setStandIn} label="Use the stand-in AI" />
      <span>
        Stand-in AI <span className="text-faint">(no API key needed, placeholder answers)</span>
      </span>
    </label>
  );
}

function FormRun({ inputStep }: { inputStep: Step | undefined }) {
  const status = useRun((s) => s.status);
  const lastInputs = useRun((s) => s.lastInputs);
  const fields = (inputStep?.settings.fields ?? []) as { name: string; type: string; description: string; example: unknown; default: unknown; required: boolean }[];
  const [values, setValues] = useState<Record<string, unknown>>(lastInputs);
  useEffect(() => setValues(lastInputs), [lastInputs]);
  const running = status === "running";
  const run = useCallback(() => void startRun(values), [values]);
  useEffect(() => {
    const handler = () => run();
    window.addEventListener("easychain:run", handler);
    return () => window.removeEventListener("easychain:run", handler);
  }, [run]);
  const fillExamples = () =>
    setValues(Object.fromEntries(fields.filter((f) => f.example != null).map((f) => [f.name, f.type === "list" || f.type === "object" ? JSON.stringify(f.example) : f.example])));
  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        run();
      }}
    >
      {fields.map((f) => (
        <Field key={f.name} label={f.name + (f.required ? "" : " (optional)")} help={f.description} htmlFor={`run-${f.name}`}>
          {f.type === "yes_no" ? (
            <Switch id={`run-${f.name}`} checked={!!values[f.name]} onCheckedChange={(v) => setValues({ ...values, [f.name]: v })} label={f.name} />
          ) : f.type === "number" ? (
            <Input id={`run-${f.name}`} type="number" value={String(values[f.name] ?? "")} placeholder={f.example != null ? String(f.example) : ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} />
          ) : (
            <Textarea
              id={`run-${f.name}`}
              rows={f.type === "text" ? 2 : 3}
              className={cn(f.type !== "text" && "font-mono text-xs")}
              value={String(values[f.name] ?? "")}
              placeholder={f.description || (f.example != null ? `e.g. ${String(f.example)}` : "")}
              onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  run();
                }
              }}
            />
          )}
        </Field>
      ))}
      {!fields.length && <p className="text-xs text-muted">This flow takes no inputs.</p>}
      <div className="flex flex-wrap items-center gap-2">
        {running ? (
          <Button variant="outline" onClick={stopRun}>
            <Square size={14} /> Stop
          </Button>
        ) : (
          <Button type="submit" variant="primary" data-testid="run-submit">
            <Play size={14} /> Run
          </Button>
        )}
        {fields.some((f) => f.example != null) && (
          <Button variant="ghost" size="sm" onClick={fillExamples}>
            Use examples
          </Button>
        )}
      </div>
    </form>
  );
}

function ChatRun() {
  const chat = useRun((s) => s.chat);
  const status = useRun((s) => s.status);
  const newChat = useRun((s) => s.newChat);
  const [text, setText] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => listRef.current?.scrollTo({ top: listRef.current.scrollHeight }), [chat]);
  const send = (message: string) => {
    if (!message.trim()) return;
    setText("");
    void startRun({}, { chatMessage: message.trim() });
  };
  return (
    <div className="flex flex-col gap-2">
      <div ref={listRef} className="scroll-thin max-h-[42vh] min-h-32 space-y-2 overflow-y-auto rounded-lg border border-border bg-surface-2/40 p-2.5" aria-live="polite" data-testid="chat-log">
        {!chat.length && <p className="py-6 text-center text-xs text-faint">Say hello to start the conversation.</p>}
        {chat.map((m, i) => (
          <div key={i} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
            <div
              className={cn(
                "max-w-[88%] rounded-2xl px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap",
                m.role === "user" ? "bg-accent text-accent-text" : m.failed ? "bg-danger-soft text-danger" : "border border-border bg-surface",
              )}
            >
              {m.content || (m.pending ? <Loader2 size={14} className="animate-spin" /> : "")}
            </div>
          </div>
        ))}
      </div>
      <form
        className="flex gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        <Input aria-label="Message" placeholder="Type a message" value={text} onChange={(e) => setText(e.target.value)} disabled={status === "running"} data-testid="chat-input" />
        <Button type="submit" variant="primary" size="icon" aria-label="Send" disabled={status === "running" || !text.trim()}>
          <Send size={15} />
        </Button>
      </form>
      <Button variant="ghost" size="sm" className="self-start" onClick={newChat}>
        <MessageSquarePlus size={13} /> New conversation
      </Button>
    </div>
  );
}

export function RunPanel() {
  const inputStep = useInputStep();
  const flowId = useFlow((s) => s.flowId);
  const chat = inputStep?.settings.mode === "chat";
  const run = useRun();
  const issues = useCheck((s) => s.issues);
  const errors = useMemo(() => issues.filter((i) => i.level === "error"), [issues]);
  const outputs = Object.entries(run.final?.status === "ok" ? run.final.output ?? {} : {});

  const retry = useCallback(() => {
    const state = useRun.getState();
    if (chat) {
      const lastUser = [...state.chat].reverse().find((m) => m.role === "user");
      if (!lastUser) return;
      const trimmed = state.chat.slice(0, state.chat.lastIndexOf(lastUser));
      useRun.setState({ chat: trimmed });
      void startRun({}, { chatMessage: lastUser.content });
    } else {
      void startRun(state.lastInputs);
    }
  }, [chat]);

  return (
    <div className="scroll-thin h-full space-y-4 overflow-y-auto p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">{chat ? "Chat with your flow" : "Run your flow"}</h2>
        {run.status === "running" && (
          <Badge tone="accent">
            <Loader2 size={11} className="animate-spin" /> Running
          </Badge>
        )}
        {run.replaying && (
          <Button size="sm" variant="ghost" onClick={() => useRun.setState({ replaying: false })}>
            Stop replay
          </Button>
        )}
      </div>
      <StandInToggle />
      {errors.length > 0 && run.status !== "running" && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-danger">Fix these before running:</p>
          <IssueList issues={errors} />
        </div>
      )}
      {chat ? <ChatRun /> : <FormRun inputStep={inputStep} />}

      {run.status !== "idle" && run.status !== "running" && run.final && (
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted" data-testid="run-summary">
          <span className={run.status === "ok" ? "text-ok" : "text-danger"}>{run.status === "ok" ? "Finished" : "Failed"}</span>
          <span>{formatMs(run.final.duration_ms)}</span>
          {!!run.final.usage?.output_tokens && <span>{formatTokens(run.final.usage.input_tokens + run.final.usage.output_tokens)} tokens</span>}
          {!!run.final.cost && <span>{formatCost(run.final.cost)}</span>}
          {run.standIn && <span className="text-warn">stand-in AI</span>}
        </div>
      )}
      {run.error && <ErrorCard error={run.error} retry={retry} />}
      {run.issues.length > 0 && <IssueList issues={run.issues} />}

      {!chat && outputs.length > 0 && (
        <section aria-label="Result" data-testid="run-output">
          <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-faint uppercase">Result</h3>
          <div className="space-y-3 rounded-lg border border-ok/30 bg-ok-soft/40 p-3">
            {outputs.map(([k, v]) => (
              <div key={k}>
                <p className="mb-0.5 font-mono text-[11px] text-muted">{k}</p>
                <ValueView value={v} />
              </div>
            ))}
          </div>
        </section>
      )}
      <Trace />
      {flowId && <RunHistory flowId={flowId} />}
      {run.status !== "idle" && run.status !== "running" && (
        <Button variant="ghost" size="sm" onClick={() => useRun.getState().reset()}>
          <RotateCcw size={13} /> Clear the run from the canvas
        </Button>
      )}
    </div>
  );
}
