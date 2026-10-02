// Inspector controls for Phase 3 steps: an Agent's tools and Add-ons, and its MCP tools.

import { ChevronDown, ChevronRight, ExternalLink, Trash2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "../../lib/api";
import { addTool, addToolStep, canAddTool, createStep, getStep, removeTool, TOOL_TYPES, updateSettings } from "../../lib/spec";
import type { McpSettings, McpTool } from "../../lib/types";
import { cn } from "../../lib/utils";
import { useCatalog } from "../../state/catalog";
import { useCheck } from "../../state/check";
import { useFlow } from "../../state/flow";
import { useUi } from "../../state/ui";
import { colorsFor, iconFor } from "../canvas/stepMeta";
import { Button, Input, Select, Switch } from "../ui";
import type { FieldProps } from "./fields";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Value = any;

/** An Agent's tools: the steps it can call, with "ask a person first" per tool. */
export function ToolsField({ value, stepId }: FieldProps) {
  const spec = useFlow((s) => s.spec)!;
  const apply = useFlow((s) => s.apply);
  const select = useUi((s) => s.select);
  const catalog = useCatalog((s) => s.catalog);
  const fields = useCheck((s) => s.analysis?.fields);
  const agent = stepId ? getStep(spec, stepId) : undefined;
  if (!agent || !stepId) return null;
  const tools: string[] = value ?? [];
  const approve: string[] = agent.settings.addons?.approve_tools ?? [];
  const toolTypes = (catalog?.steps ?? []).filter((s) => TOOL_TYPES.has(s.type));
  const existing = spec.steps.filter((s) => !tools.includes(s.id) && canAddTool(spec, s.id, stepId) === null);

  const setApproval = (toolId: string, on: boolean) =>
    apply((s) =>
      updateSettings(s, stepId, {
        addons: { ...(agent.settings.addons ?? {}), approve_tools: on ? [...approve, toolId] : approve.filter((t) => t !== toolId) },
      }),
    );

  return (
    <div className="space-y-2" data-testid="agent-tools">
      {!tools.length && (
        <p className="rounded-lg border border-dashed border-border px-3 py-2 text-xs text-muted">
          No tools yet. Add one below, or drag a step's purple handle onto the agent's <strong>Tools</strong> handle.
        </p>
      )}
      {tools.map((toolId) => {
        const tool = getStep(spec, toolId);
        const info = catalog?.steps.find((s) => s.type === tool?.type);
        const Icon = iconFor(info?.icon);
        return (
          <div key={toolId} className="space-y-1.5 rounded-lg border border-border bg-surface-2/50 p-2" data-testid={`agent-tool-${toolId}`}>
            <div className="flex items-center gap-2">
              <span className={cn("flex h-6 w-6 shrink-0 items-center justify-center rounded", colorsFor(info?.category).chip)}>
                <Icon size={13} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{tool?.name || toolId}</p>
                <p className="truncate font-mono text-[11px] text-faint">{toolId}</p>
              </div>
              <Button size="icon-sm" variant="ghost" aria-label={`Open ${tool?.name || toolId}`} onClick={() => select([toolId])}>
                <ExternalLink size={13} />
              </Button>
              <Button size="icon-sm" variant="ghost" aria-label={`Remove the tool ${tool?.name || toolId}`} onClick={() => apply((s) => removeTool(s, stepId, toolId))}>
                <Trash2 size={13} />
              </Button>
            </div>
            {tool?.description ? (
              <p className="line-clamp-2 text-xs text-muted">{tool.description}</p>
            ) : (
              <p className="text-xs text-warn">
                Describe this tool so the agent knows when to use it.{" "}
                <button type="button" className="font-medium underline" onClick={() => useUi.getState().focus(toolId, "description")}>
                  Describe it
                </button>
              </p>
            )}
            <label className="flex items-center gap-2 text-xs text-muted">
              <Switch checked={approve.includes(toolId)} onCheckedChange={(on) => setApproval(toolId, on)} label={`Ask a person before ${toolId}`} />
              Ask a person before each call
            </label>
          </div>
        );
      })}
      <div className="flex flex-wrap gap-1.5">
        <Select
          aria-label="Add a new tool"
          className="h-8 w-auto flex-1 text-xs"
          value=""
          onChange={(e) => {
            const info = toolTypes.find((t) => t.type === e.target.value);
            if (!info) return;
            const step = createStep(info, spec, fields?.map((f) => f.name));
            apply((s) => addToolStep(s, stepId, step));
            select([step.id]);
          }}
        >
          <option value="">+ Add a new tool…</option>
          {toolTypes.map((t) => (
            <option key={t.type} value={t.type}>
              {t.label}
            </option>
          ))}
        </Select>
        {existing.length > 0 && (
          <Select
            aria-label="Use a step as a tool"
            className="h-8 w-auto flex-1 text-xs"
            value=""
            onChange={(e) => e.target.value && apply((s) => addTool(s, stepId, e.target.value))}
          >
            <option value="">Use a step on the canvas…</option>
            {existing.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name || s.id}
              </option>
            ))}
          </Select>
        )}
      </div>
    </div>
  );
}

function Addon({
  title,
  technical,
  on,
  onToggle,
  children,
  testId,
}: {
  title: string;
  technical: string;
  on: boolean;
  onToggle?: (on: boolean) => void;
  children?: ReactNode;
  testId: string;
}) {
  const mode = useUi((s) => s.mode);
  return (
    <div className={cn("rounded-lg border px-2.5 py-2", on ? "border-accent/40 bg-accent-soft/40" : "border-border")} data-testid={`addon-${testId}`}>
      <div className="flex items-center gap-2">
        {onToggle && <Switch checked={on} onCheckedChange={onToggle} label={title} />}
        <span className="flex-1 text-xs font-medium">{title}</span>
        {mode === "pro" && <span className="font-mono text-[10px] text-faint">{technical}</span>}
      </div>
      {on && children && <div className="mt-2 space-y-1.5 pl-1">{children}</div>}
    </div>
  );
}

function NumberInput({ label, value, onChange, min = 1 }: { label: string; value: number | null | undefined; onChange: (v: number | null) => void; min?: number }) {
  return (
    <label className="flex items-center gap-2 text-xs text-muted">
      <span className="flex-1">{label}</span>
      <Input
        aria-label={label}
        type="number"
        min={min}
        className="h-7 w-24 text-xs"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      />
    </label>
  );
}

const PII = [
  { value: "email", label: "Email addresses" },
  { value: "credit_card", label: "Card numbers" },
  { value: "ip", label: "IP addresses" },
  { value: "mac_address", label: "MAC addresses" },
  { value: "url", label: "Web addresses" },
];

/** Agent Add-ons: middleware toggles, each with its few settings. */
export function AddonsField({ value, onChange, settings }: FieldProps) {
  const [open, setOpen] = useState(false);
  const a: Record<string, Value> = value ?? {};
  const set = (patch: Record<string, unknown>) => onChange({ ...a, ...patch });
  const tools: string[] = [...(settings.tools ?? []), ...(settings.mcp ?? []).flatMap((m: { tools: string[] }) => m.tools ?? [])];
  const active = [
    a.approve_tools?.length,
    a.max_model_calls,
    a.max_tool_calls,
    a.tool_retries,
    a.model_retries,
    a.fallback_models?.length,
    a.summarise,
    a.clear_tool_results,
    a.pii?.length,
    a.select_tools,
    a.todo_list,
    a.memory,
    a.emulate_tools,
  ].filter(Boolean).length;
  return (
    <div className="space-y-1.5" data-testid="agent-addons">
      <button type="button" className="flex items-center gap-1 text-xs font-medium text-muted hover:text-text" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {active ? `${active} add-on${active === 1 ? "" : "s"} on` : "No add-ons on"} · {open ? "hide" : "show all"}
      </button>
      {open && (
        <div className="space-y-1.5">
          <Addon title="Ask a person before using tools" technical="HumanInTheLoopMiddleware" on={!!a.approve_tools?.length} testId="approve">
            {null}
          </Addon>
          {tools.length > 0 ? (
            <div className="-mt-1 space-y-1 rounded-b-lg border border-t-0 border-border px-2.5 py-1.5">
              {tools.map((t) => (
                <label key={t} className="flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    className="accent-[var(--accent)]"
                    checked={(a.approve_tools ?? []).includes(t)}
                    onChange={(e) =>
                      set({ approve_tools: e.target.checked ? [...(a.approve_tools ?? []), t] : (a.approve_tools ?? []).filter((x: string) => x !== t) })
                    }
                  />
                  <span className="font-mono">{t}</span>
                </label>
              ))}
              <p className="text-[11px] text-faint">The run waits in the Inbox until someone approves, edits or rejects each call.</p>
            </div>
          ) : (
            <p className="-mt-1 px-2.5 text-[11px] text-faint">Add tools first.</p>
          )}
          <Addon title="Limits" technical="ModelCallLimitMiddleware · ToolCallLimitMiddleware" on testId="limits">
            <NumberInput label="Most model calls per run" value={a.max_model_calls} onChange={(v) => set({ max_model_calls: v })} />
            <NumberInput label="Most tool calls per run" value={a.max_tool_calls} onChange={(v) => set({ max_tool_calls: v })} />
          </Addon>
          <Addon title="When a tool fails" technical="ToolErrorMiddleware · ToolRetryMiddleware" on testId="errors">
            <Select aria-label="When a tool fails" className="h-7 text-xs" value={a.tool_errors ?? "tell_agent"} onChange={(e) => set({ tool_errors: e.target.value })}>
              <option value="tell_agent">Tell the agent, so it can try another way</option>
              <option value="stop">Stop the run</option>
            </Select>
            <NumberInput label="Try a failing tool again" min={0} value={a.tool_retries ?? 0} onChange={(v) => set({ tool_retries: v ?? 0 })} />
            <NumberInput label="Try a failing model call again" min={0} value={a.model_retries ?? 0} onChange={(v) => set({ model_retries: v ?? 0 })} />
          </Addon>
          <Addon
            title="Fall back to other models"
            technical="ModelFallbackMiddleware"
            on={!!a.fallback_models?.length}
            onToggle={(on) => set({ fallback_models: on ? ["anthropic:claude-haiku-4-5"] : [] })}
            testId="fallback"
          >
            <Input
              aria-label="Fallback models"
              className="h-7 font-mono text-xs"
              defaultValue={(a.fallback_models ?? []).join(", ")}
              placeholder="anthropic:claude-haiku-4-5, ollama:llama3.2"
              onBlur={(e) => set({ fallback_models: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })}
            />
          </Addon>
          <Addon title="Keep long conversations short" technical="SummarizationMiddleware" on={!!a.summarise} onToggle={(on) => set({ summarise: on })} testId="summarise">
            <NumberInput label="Summarise after (tokens)" value={a.summarise_after ?? 4000} onChange={(v) => set({ summarise_after: v ?? 4000 })} />
            <NumberInput label="Recent messages to keep" value={a.summarise_keep ?? 20} onChange={(v) => set({ summarise_keep: v ?? 20 })} />
          </Addon>
          <Addon
            title="Clear old tool results"
            technical="ContextEditingMiddleware"
            on={!!a.clear_tool_results}
            onToggle={(on) => set({ clear_tool_results: on ? 50000 : null })}
            testId="clear"
          >
            <NumberInput label="When the conversation passes (tokens)" value={a.clear_tool_results} onChange={(v) => set({ clear_tool_results: v })} />
          </Addon>
          <Addon title="Protect personal data" technical="PIIMiddleware" on={!!a.pii?.length} onToggle={(on) => set({ pii: on ? ["email"] : [] })} testId="pii">
            {PII.map((p) => (
              <label key={p.value} className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  className="accent-[var(--accent)]"
                  checked={(a.pii ?? []).includes(p.value)}
                  onChange={(e) => set({ pii: e.target.checked ? [...(a.pii ?? []), p.value] : (a.pii ?? []).filter((x: string) => x !== p.value) })}
                />
                {p.label}
              </label>
            ))}
            <Select aria-label="What to do with it" className="h-7 text-xs" value={a.pii_strategy ?? "redact"} onChange={(e) => set({ pii_strategy: e.target.value })}>
              <option value="redact">Replace it with [REDACTED]</option>
              <option value="mask">Mask it (show the last digits)</option>
              <option value="hash">Replace it with a code</option>
              <option value="block">Stop the run</option>
            </Select>
          </Addon>
          <Addon
            title="Pick the relevant tools each time"
            technical="LLMToolSelectorMiddleware"
            on={!!a.select_tools}
            onToggle={(on) => set({ select_tools: on ? 5 : null })}
            testId="select"
          >
            <NumberInput label="Tools to offer the agent" value={a.select_tools} onChange={(v) => set({ select_tools: v })} />
          </Addon>
          <Addon title="Plan with a to-do list" technical="TodoListMiddleware" on={!!a.todo_list} onToggle={(on) => set({ todo_list: on })} testId="todo" />
          <Addon title="Remember the user across conversations" technical="LangGraph store" on={!!a.memory} onToggle={(on) => set({ memory: on })} testId="memory" />
          <Addon title="Pretend tools (for testing)" technical="LLMToolEmulator" on={!!a.emulate_tools} onToggle={(on) => set({ emulate_tools: on })} testId="emulate" />
        </div>
      )}
    </div>
  );
}

/** MCP tools for an Agent: servers from Settings, and which of their tools to use. */
export function McpToolsField({ value, onChange }: FieldProps) {
  const openSettings = useUi((s) => s.openSettings);
  const [settings, setSettings] = useState<McpSettings | null>(null);
  const [tools, setTools] = useState<Record<string, McpTool[] | string>>({});
  const chosen: { server: string; tools: string[] }[] = value ?? [];
  useEffect(() => {
    api.mcpSettings().then(setSettings).catch(() => setSettings({ servers: [], allowed_commands: [] }));
  }, []);
  const load = (server: string) => {
    setTools((t) => ({ ...t, [server]: "loading" }));
    api
      .mcpTools({ server_id: server })
      .then((r) => setTools((t) => ({ ...t, [server]: r.tools })))
      .catch((err) => setTools((t) => ({ ...t, [server]: err instanceof ApiError ? err.message : String(err) })));
  };
  if (!settings) return <p className="text-xs text-faint">Loading…</p>;
  if (!settings.servers.length)
    return (
      <p className="text-xs text-muted">
        No MCP servers yet.{" "}
        <button type="button" className="font-medium text-accent underline" onClick={() => openSettings({ tab: "mcp" })}>
          Add one in Settings
        </button>
      </p>
    );
  const entry = (server: string) => chosen.find((c) => c.server === server);
  const write = (server: string, next: string[] | null) =>
    onChange(next === null ? chosen.filter((c) => c.server !== server) : [...chosen.filter((c) => c.server !== server), { server, tools: next }]);
  return (
    <div className="space-y-1.5">
      {settings.servers.map((server) => {
        const on = !!entry(server.id);
        const list = tools[server.id];
        return (
          <div key={server.id} className="rounded-lg border border-border px-2.5 py-2">
            <label className="flex items-center gap-2 text-xs font-medium">
              <Switch
                checked={on}
                label={`Use ${server.name || server.id}`}
                onCheckedChange={(v) => {
                  write(server.id, v ? [] : null);
                  if (v && !list) load(server.id);
                }}
              />
              {server.name || server.id}
              <span className="font-mono text-[10px] text-faint">{server.transport}</span>
            </label>
            {on && (
              <div className="mt-1.5 space-y-1 pl-1 text-xs">
                {list === "loading" && <p className="text-faint">Asking the server for its tools…</p>}
                {typeof list === "string" && list !== "loading" && <p className="text-danger">{list}</p>}
                {Array.isArray(list) && (
                  <>
                    <p className="text-faint">{entry(server.id)?.tools.length ? "Only these tools:" : "All its tools (or pick some):"}</p>
                    {list.map((t) => (
                      <label key={t.name} className="flex items-start gap-2">
                        <input
                          type="checkbox"
                          className="mt-0.5 accent-[var(--accent)]"
                          checked={entry(server.id)?.tools.includes(t.name) ?? false}
                          onChange={(e) => {
                            const cur = entry(server.id)?.tools ?? [];
                            write(server.id, e.target.checked ? [...cur, t.name] : cur.filter((x) => x !== t.name));
                          }}
                        />
                        <span>
                          <span className="font-mono">{t.name}</span>
                          {t.description && <span className="block text-faint">{t.description}</span>}
                        </span>
                      </label>
                    ))}
                  </>
                )}
                {!list && (
                  <button type="button" className="text-accent underline" onClick={() => load(server.id)}>
                    Show its tools
                  </button>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
