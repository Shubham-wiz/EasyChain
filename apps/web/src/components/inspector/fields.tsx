// Form controls for the step inspector, one per FormField.kind in the step catalog.

import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../../lib/api";
import { toIdent } from "../../lib/spec";
import type { FieldInfo, FlowListItem, FormField, ProviderInfo } from "../../lib/types";
import { cn } from "../../lib/utils";
import { useCatalog } from "../../state/catalog";
import { useFlow } from "../../state/flow";
import { useUi } from "../../state/ui";
import { CodeView } from "../CodeView";
import { AddonsField, McpToolsField, ToolsField } from "./AgentFields";
import { KnowledgeBasePicker, McpServerPicker, McpToolPicker, SecretPicker, SqlEditor } from "./IntegrationFields";
import { SchemaBuilder } from "./SchemaBuilder";
import { Button, Input, Select, Switch, Textarea } from "../ui";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Value = any;

export interface FieldProps {
  field: FormField;
  value: Value;
  onChange: (value: Value) => void;
  settings: Record<string, Value>;
  fields: FieldInfo[];
  upstream?: string | null;
  id: string;
  /** The step being edited (controls that change more than their own setting need it). */
  stepId?: string;
}

const VAR_RE = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g;

/** Text box with {variables}: shows which variables are used and lets you insert fields. */
export function TemplateInput({ value, onChange, fields, id, field }: FieldProps) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const text = String(value ?? "");
  const used = useMemo(() => Array.from(new Set(Array.from(text.matchAll(VAR_RE), (m) => m[1]))), [text]);
  const known = new Set(fields.map((f) => f.name));
  const singleLine = field.key === "url";
  const insert = (name: string) => {
    const el = ref.current;
    const token = `{${name}}`;
    if (!el) return onChange(text + token);
    const start = el.selectionStart ?? text.length;
    const end = el.selectionEnd ?? text.length;
    onChange(text.slice(0, start) + token + text.slice(end));
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(start + token.length, start + token.length);
    });
  };
  const available = fields.filter((f) => f.type !== "messages");
  return (
    <div className="space-y-1.5">
      <Textarea
        ref={ref}
        id={id}
        value={text}
        rows={singleLine ? 1 : Math.min(10, Math.max(3, text.split("\n").length + 1))}
        className={cn(singleLine && "min-h-9 resize-none py-1.5 font-mono text-[13px]")}
        placeholder={field.placeholder ?? field.example}
        onChange={(e) => onChange(singleLine ? e.target.value.replace(/\n/g, "") : e.target.value)}
        spellCheck={!singleLine}
      />
      <div className="flex flex-wrap items-center gap-1">
        {used.map((name) => (
          <span
            key={name}
            className={cn(
              "rounded px-1.5 py-px font-mono text-[11px]",
              known.has(name) ? "bg-accent-soft text-accent" : "bg-danger-soft text-danger",
            )}
            title={known.has(name) ? "Flow Data field" : "Not a Flow Data field yet"}
          >
            {`{${name}}`}
          </span>
        ))}
        {available.length > 0 && (
          <select
            aria-label="Insert a field"
            className="h-6 cursor-pointer rounded border border-dashed border-border bg-transparent px-1 text-[11px] text-muted"
            value=""
            onChange={(e) => e.target.value && insert(e.target.value)}
          >
            <option value="">+ Insert field</option>
            {available.map((f) => (
              <option key={f.name} value={f.name}>
                {f.name}
              </option>
            ))}
          </select>
        )}
      </div>
    </div>
  );
}

export function ModelPicker({ value, onChange, id }: FieldProps) {
  const providers = useCatalog((s) => s.catalog?.providers ?? []);
  const openSettings = useUi((s) => s.openSettings);
  const [providerId, ...rest] = String(value ?? "").split(":");
  const model = rest.join(":");
  const provider: ProviderInfo | undefined = providers.find((p) => p.id === providerId);
  const listId = `${id}-models`;
  return (
    <div className="space-y-1.5">
      <div className="flex gap-1.5">
        <Select
          aria-label="Provider"
          className="w-[8.5rem] shrink-0"
          value={providerId}
          onChange={(e) => {
            const next = providers.find((p) => p.id === e.target.value);
            onChange(`${e.target.value}:${next?.models[0]?.id.split(":").slice(1).join(":") ?? ""}`);
          }}
        >
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
          {!provider && providerId && <option value={providerId}>{providerId}</option>}
        </Select>
        <Input
          id={id}
          aria-label="Model"
          list={listId}
          value={model}
          placeholder="model name"
          onChange={(e) => onChange(`${providerId}:${e.target.value.trim()}`)}
        />
        <datalist id={listId}>
          {provider?.models.map((m) => (
            <option key={m.id} value={m.id.split(":").slice(1).join(":")}>
              {m.label}
            </option>
          ))}
        </datalist>
      </div>
      {provider && !provider.key_set && (
        <p className="text-xs text-warn">
          No {provider.key_label} yet.{" "}
          <button type="button" className="font-medium underline" onClick={() => openSettings({ provider: provider.id })}>
            Add your API key
          </button>
        </p>
      )}
    </div>
  );
}

export function FieldPicker({ value, onChange, fields, upstream, field, id }: FieldProps) {
  const options = fields.filter((f) => (field.key === "history" ? f.type === "messages" || f.type === "any" : true));
  const current = value ?? "";
  const auto = field.key === "history" ? "None" : `From the previous step${upstream ? ` (${upstream})` : ""}`;
  return (
    <Select id={id} value={current} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">{auto}</option>
      {options.map((f) => (
        <option key={f.name} value={f.name}>
          {f.name} · {f.type}
        </option>
      ))}
      {current && !options.some((f) => f.name === current) && <option value={current}>{current}</option>}
    </Select>
  );
}

export function FieldNameInput({ value, onChange, id, field }: FieldProps) {
  const [draft, setDraft] = useState(String(value ?? ""));
  useEffect(() => setDraft(String(value ?? "")), [value]);
  return (
    <Input
      id={id}
      className="font-mono text-[13px]"
      value={draft}
      placeholder={field.example}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        const clean = toIdent(draft, "result");
        setDraft(clean);
        if (clean !== value) onChange(clean);
      }}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
    />
  );
}

function Row({ children, onRemove, onUp, onDown, label }: { children: ReactNode; onRemove: () => void; onUp?: () => void; onDown?: () => void; label: string }) {
  return (
    <div className="space-y-1.5 rounded-lg border border-border bg-surface-2/50 p-2">
      {children}
      <div className="flex justify-end gap-0.5">
        {onUp && (
          <Button size="icon-sm" variant="ghost" aria-label={`Move ${label} up`} onClick={onUp}>
            <ArrowUp size={13} />
          </Button>
        )}
        {onDown && (
          <Button size="icon-sm" variant="ghost" aria-label={`Move ${label} down`} onClick={onDown}>
            <ArrowDown size={13} />
          </Button>
        )}
        <Button size="icon-sm" variant="ghost" aria-label={`Remove ${label}`} onClick={onRemove}>
          <Trash2 size={13} />
        </Button>
      </div>
    </div>
  );
}

function move<T>(list: T[], i: number, by: number): T[] {
  const next = [...list];
  const [item] = next.splice(i, 1);
  next.splice(i + by, 0, item);
  return next;
}

export function InputFieldsEditor({ value, onChange, field }: FieldProps) {
  const list: Value[] = value ?? [];
  const set = (i: number, patch: Record<string, unknown>) => onChange(list.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  const taken = new Set(list.map((f) => f.name));
  const nextName = () => {
    let n = list.length + 1;
    while (taken.has(`field_${n}`)) n += 1;
    return list.length ? `field_${n}` : "question";
  };
  return (
    <div className="space-y-2">
      {list.map((f, i) => (
        <Row key={i} label={`field ${f.name}`} onRemove={() => onChange(list.filter((_, j) => j !== i))}>
          <div className="flex gap-1.5">
            <Input
              aria-label="Field name"
              className="font-mono text-[13px]"
              defaultValue={f.name}
              onBlur={(e) => {
                const clean = toIdent(e.target.value, "field");
                e.target.value = clean;
                if (clean !== f.name) set(i, { name: clean });
              }}
            />
            <Select aria-label="Field type" className="w-28" value={f.type} onChange={(e) => set(i, { type: e.target.value })}>
              {(field.options ?? []).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </div>
          <Input aria-label="Description" placeholder="What it is (shown on the run form)" value={f.description ?? ""} onChange={(e) => set(i, { description: e.target.value })} />
          <div className="flex items-center gap-1.5">
            <Input
              aria-label="Example"
              placeholder="Example value"
              value={f.example ?? ""}
              onChange={(e) => set(i, { example: e.target.value || null })}
            />
            <label className="flex shrink-0 items-center gap-1.5 text-xs text-muted">
              <Switch checked={f.required ?? true} onCheckedChange={(v) => set(i, { required: v })} label="Required" />
              Required
            </label>
          </div>
        </Row>
      ))}
      <Button
        size="sm"
        variant="outline"
        onClick={() => onChange([...list, { name: nextName(), type: "text", description: "", example: null, default: null, required: true }])}
      >
        <Plus size={13} /> Add a field
      </Button>
    </div>
  );
}

export function OutputFieldsPicker({ value, onChange, fields }: FieldProps) {
  const chosen: string[] = value ?? [];
  const names = Array.from(new Set([...fields.filter((f) => !f.is_input || f.written_by.length).map((f) => f.name), ...chosen]));
  if (!names.length) return <p className="text-xs text-muted">Connect steps first; their results show up here.</p>;
  return (
    <div className="space-y-1">
      {names.map((name) => (
        <label key={name} className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-sm hover:bg-surface-2">
          <input
            type="checkbox"
            className="accent-[var(--accent)]"
            checked={chosen.includes(name)}
            onChange={(e) => onChange(e.target.checked ? [...chosen, name] : chosen.filter((n) => n !== name))}
          />
          <span className="font-mono text-[13px]">{name}</span>
          <span className="text-[11px] text-faint">{fields.find((f) => f.name === name)?.type}</span>
        </label>
      ))}
    </div>
  );
}

const KEY_VALUE_LABELS: Record<string, { key: string; value: string; add: string; first: () => string }> = {
  headers: { key: "Header name", value: "Header value", add: "Add a header", first: () => "X-Header" },
  inputs: { key: "Its input", value: "Value", add: "Give it a value", first: () => "input" },
  outputs: { key: "Field here", value: "Its result", add: "Save a result", first: () => "result" },
  arguments: { key: "Argument", value: "Value ({field} works)", add: "Add an argument", first: () => "argument" },
};

export function KeyValueEditor({ value, onChange, field }: FieldProps) {
  const labels = KEY_VALUE_LABELS[field.key] ?? KEY_VALUE_LABELS.headers;
  const entries = Object.entries((value ?? {}) as Record<string, string>);
  const write = (list: [string, string][]) => onChange(Object.fromEntries(list));
  const clean = (k: string) => (field.key === "headers" ? k : toIdent(k, "field"));
  return (
    <div className="space-y-1.5">
      {entries.map(([k, v], i) => (
        <div key={`${k}-${i}`} className="flex gap-1.5">
          <Input
            aria-label={labels.key}
            className={cn("w-2/5", field.key !== "headers" && "font-mono text-[12px]")}
            defaultValue={k}
            onBlur={(e) => write(entries.map((p, j) => (j === i ? [clean(e.target.value), p[1]] : p)))}
          />
          <Input
            aria-label={labels.value}
            className="font-mono text-[12px]"
            value={v}
            onChange={(e) => write(entries.map((p, j) => (j === i ? [p[0], e.target.value] : p)))}
          />
          <Button size="icon" variant="ghost" aria-label={`Remove ${labels.key.toLowerCase()}`} onClick={() => write(entries.filter((_, j) => j !== i))}>
            <Trash2 size={13} />
          </Button>
        </div>
      ))}
      <Button size="sm" variant="outline" onClick={() => write([...entries, [`${labels.first()}_${entries.length + 1}`.replace("X-Header_", "X-Header-"), ""]])}>
        <Plus size={13} /> {labels.add}
      </Button>
    </div>
  );
}

/** Jump: fields to set, each to text with {placeholders} or (Pro) an expression. */
export function FieldUpdatesEditor({ value, onChange, fields }: FieldProps) {
  const mode = useUi((s) => s.mode);
  const list: { field: string; value?: string; expression?: string | null }[] = value ?? [];
  const set = (i: number, patch: Record<string, unknown>) => onChange(list.map((u, j) => (j === i ? { ...u, ...patch } : u)));
  const listId = "field-updates-names";
  return (
    <div className="space-y-2">
      <datalist id={listId}>
        {fields.map((f) => (
          <option key={f.name} value={f.name} />
        ))}
      </datalist>
      {list.map((u, i) => {
        const usesExpression = u.expression != null;
        return (
          <Row key={i} label={`update of ${u.field}`} onRemove={() => onChange(list.filter((_, j) => j !== i))}>
            <div className="flex items-center gap-1.5">
              <Input
                aria-label="Field to set"
                list={listId}
                className="w-2/5 font-mono text-[12px]"
                defaultValue={u.field}
                onBlur={(e) => {
                  const clean = toIdent(e.target.value, "field");
                  e.target.value = clean;
                  if (clean !== u.field) set(i, { field: clean });
                }}
              />
              <span className="text-xs text-muted">=</span>
              {usesExpression ? (
                <Input aria-label="Expression" className="font-mono text-[12px]" value={u.expression ?? ""} onChange={(e) => set(i, { expression: e.target.value })} />
              ) : (
                <Input aria-label="New value" className="font-mono text-[12px]" placeholder="text or {field}" value={u.value ?? ""} onChange={(e) => set(i, { value: e.target.value })} />
              )}
            </div>
            {mode === "pro" && (
              <button
                type="button"
                className="text-[11px] text-accent underline"
                onClick={() => set(i, usesExpression ? { expression: null } : { expression: `${u.field} + 1`, value: "" })}
              >
                {usesExpression ? "Use text" : "Use an expression"}
              </button>
            )}
          </Row>
        );
      })}
      <Button size="sm" variant="outline" onClick={() => onChange([...list, { field: `field_${list.length + 1}`, value: "" }])}>
        <Plus size={13} /> Set a field
      </Button>
    </div>
  );
}

/** Sub-flow: pick another flow of the workspace. */
export function FlowPicker({ value, onChange, id }: FieldProps) {
  const flowId = useFlow((s) => s.flowId);
  const [flows, setFlows] = useState<FlowListItem[] | null>(null);
  useEffect(() => {
    api.flows().then(setFlows).catch(() => setFlows([]));
  }, []);
  const options = (flows ?? []).filter((f) => f.id !== flowId);
  return (
    <div className="flex gap-1.5">
      <Select id={id} value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        <option value="">{flows ? (options.length ? "Pick a flow" : "No other flows yet") : "Loading…"}</option>
        {options.map((f) => (
          <option key={f.id} value={f.id}>
            {f.name}
          </option>
        ))}
        {value && !options.some((f) => f.id === value) && <option value={value}>{value}</option>}
      </Select>
      {value && (
        <Button variant="outline" size="sm" onClick={() => (window.location.hash = `#/flows/${value}`)}>
          Open
        </Button>
      )}
    </div>
  );
}

export function StringList({ value, onChange, id, field }: FieldProps) {
  const list: string[] = value ?? [];
  return (
    <Input
      id={id}
      defaultValue={list.join(", ")}
      placeholder={field.placeholder ?? "Separate with commas"}
      onBlur={(e) =>
        onChange(
          e.target.value
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        )
      }
    />
  );
}

export function ExamplesEditor({ value, onChange }: FieldProps) {
  const list: { role: string; content: string }[] = value ?? [];
  const set = (i: number, patch: Record<string, string>) => onChange(list.map((e, j) => (j === i ? { ...e, ...patch } : e)));
  return (
    <div className="space-y-2">
      {list.map((ex, i) => (
        <Row key={i} label={`example ${i + 1}`} onRemove={() => onChange(list.filter((_, j) => j !== i))}>
          <Select aria-label="Who says it" value={ex.role} onChange={(e) => set(i, { role: e.target.value })}>
            <option value="user">The user says</option>
            <option value="assistant">The AI answers</option>
          </Select>
          <Textarea aria-label="Example text" rows={2} value={ex.content} onChange={(e) => set(i, { content: e.target.value })} />
        </Row>
      ))}
      <Button
        size="sm"
        variant="outline"
        onClick={() => onChange([...list, { role: list.at(-1)?.role === "user" ? "assistant" : "user", content: "" }])}
      >
        <Plus size={13} /> Add an example
      </Button>
    </div>
  );
}

const OPS: { value: string; label: string; noValue?: boolean }[] = [
  { value: "contains", label: "contains" },
  { value: "not_contains", label: "doesn't contain" },
  { value: "equals", label: "is" },
  { value: "not_equals", label: "is not" },
  { value: "starts_with", label: "starts with" },
  { value: "ends_with", label: "ends with" },
  { value: "matches", label: "matches pattern" },
  { value: "is_empty", label: "is empty", noValue: true },
  { value: "is_not_empty", label: "is not empty", noValue: true },
  { value: "greater_than", label: "is more than" },
  { value: "less_than", label: "is less than" },
  { value: "longer_than", label: "is longer than" },
  { value: "shorter_than", label: "is shorter than" },
  { value: "is_true", label: "is yes", noValue: true },
  { value: "is_false", label: "is no", noValue: true },
];

export function ExitsEditor({ value, onChange, settings, fields }: FieldProps) {
  const mode = useUi((s) => s.mode);
  const list: Value[] = value ?? [];
  const ai = settings.mode === "ai";
  const set = (i: number, patch: Record<string, unknown>) => onChange(list.map((e, j) => (j === i ? { ...e, ...patch } : e)));
  const setWhen = (i: number, patch: Record<string, unknown>) => set(i, { when: { field: null, op: "contains", value: null, expression: null, ...(list[i].when ?? {}), ...patch } });
  return (
    <div className="space-y-2">
      {list.map((ex, i) => {
        const when = ex.when ?? null;
        const op = OPS.find((o) => o.value === when?.op);
        const usesExpression = when?.expression != null;
        return (
          <Row
            key={i}
            label={`exit ${ex.label}`}
            onRemove={() => onChange(list.filter((_, j) => j !== i))}
            onUp={i > 0 ? () => onChange(move(list, i, -1)) : undefined}
            onDown={i < list.length - 1 ? () => onChange(move(list, i, 1)) : undefined}
          >
            <Input aria-label="Exit name" value={ex.label} onChange={(e) => set(i, { label: e.target.value })} className="font-medium" />
            {ai ? (
              <Input
                aria-label="When to take this exit"
                placeholder="When to take it, e.g. the customer is unhappy"
                value={ex.description ?? ""}
                onChange={(e) => set(i, { description: e.target.value })}
              />
            ) : usesExpression ? (
              <Input
                aria-label="Expression"
                className="font-mono text-[12px]"
                value={when.expression}
                onChange={(e) => setWhen(i, { expression: e.target.value })}
              />
            ) : (
              <div className="flex flex-wrap gap-1.5">
                <span className="self-center text-xs text-muted">When</span>
                <Select aria-label="Field to check" className="h-8 w-auto flex-1" value={when?.field ?? ""} onChange={(e) => setWhen(i, { field: e.target.value || null })}>
                  <option value="">pick a field</option>
                  {fields.map((f) => (
                    <option key={f.name} value={f.name}>
                      {f.name}
                    </option>
                  ))}
                </Select>
                <Select aria-label="Check" className="h-8 w-auto" value={when?.op ?? "contains"} onChange={(e) => setWhen(i, { op: e.target.value })}>
                  {OPS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
                {!op?.noValue && (
                  <Input
                    aria-label="Value"
                    className="h-8"
                    placeholder="value"
                    value={when?.value ?? ""}
                    onChange={(e) => setWhen(i, { value: e.target.value })}
                  />
                )}
              </div>
            )}
            {!ai && mode === "pro" && (
              <button
                type="button"
                className="text-[11px] text-accent underline"
                onClick={() => setWhen(i, usesExpression ? { expression: null } : { expression: when?.field ? `len(${when.field}) > 0` : "True" })}
              >
                {usesExpression ? "Use a simple rule" : "Use an expression"}
              </button>
            )}
          </Row>
        );
      })}
      <Button
        size="sm"
        variant="outline"
        onClick={() =>
          onChange([
            ...list,
            ai ? { label: `Exit ${list.length + 1}`, description: "" } : { label: `Exit ${list.length + 1}`, when: { field: null, op: "contains", value: "" } },
          ])
        }
      >
        <Plus size={13} /> Add an exit
      </Button>
    </div>
  );
}

export function renderControl(props: FieldProps): ReactNode {
  const { field, value, onChange, id } = props;
  switch (field.kind) {
    case "template":
      return <TemplateInput {...props} />;
    case "textarea":
      return <Textarea id={id} rows={3} value={value ?? ""} placeholder={field.placeholder ?? field.example} onChange={(e) => onChange(e.target.value)} />;
    case "text":
      return <Input id={id} value={value ?? ""} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value || (field.key === "base_url" ? null : ""))} />;
    case "number":
      return (
        <Input
          id={id}
          type="number"
          min={field.min}
          max={field.max}
          step={field.step ?? "any"}
          value={value ?? ""}
          placeholder="Default"
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        />
      );
    case "slider":
      return (
        <div className="flex items-center gap-2">
          <input
            id={id}
            type="range"
            className="flex-1 accent-[var(--accent)]"
            min={field.min}
            max={field.max}
            step={field.step}
            value={value ?? 0.7}
            onChange={(e) => onChange(Number(e.target.value))}
          />
          <span className="w-16 text-right text-xs text-muted">{value == null ? "default" : value}</span>
          {value != null && (
            <button type="button" className="text-[11px] text-accent underline" onClick={() => onChange(null)}>
              reset
            </button>
          )}
        </div>
      );
    case "select":
      return (
        <Select id={id} value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}>
          {(field.options ?? []).map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
      );
    case "switch":
      return <Switch id={id} checked={!!value} onCheckedChange={onChange} label={field.label} />;
    case "model":
      return <ModelPicker {...props} />;
    case "field":
      return <FieldPicker {...props} />;
    case "field_name":
      return <FieldNameInput {...props} />;
    case "input_fields":
      return <InputFieldsEditor {...props} />;
    case "field_multi":
      return <OutputFieldsPicker {...props} />;
    case "key_value":
      return <KeyValueEditor {...props} />;
    case "string_list":
      return <StringList {...props} />;
    case "examples":
      return <ExamplesEditor {...props} />;
    case "exits":
      return <ExitsEditor {...props} />;
    case "field_updates":
      return <FieldUpdatesEditor {...props} />;
    case "flow":
      return <FlowPicker {...props} />;
    case "code":
      return <CodeView value={value ?? ""} onChange={onChange} height={260} label="Python code" />;
    case "schema":
      return <SchemaBuilder {...props} />;
    case "tools":
      return <ToolsField {...props} />;
    case "agent_addons":
      return <AddonsField {...props} />;
    case "mcp_tools":
      return <McpToolsField {...props} />;
    case "knowledge_base":
      return <KnowledgeBasePicker {...props} />;
    case "secret":
      return <SecretPicker {...props} />;
    case "sql":
      return <SqlEditor {...props} />;
    case "mcp_server":
      return <McpServerPicker {...props} />;
    case "mcp_tool":
      return <McpToolPicker {...props} />;
    default:
      return <Input id={id} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />;
  }
}

export function isVisible(field: FormField, settings: Record<string, Value>): boolean {
  if (!field.show_if) return true;
  // "*" means: shown when that setting has any value.
  return Object.entries(field.show_if).every(([key, expected]) =>
    Array.isArray(expected)
      ? expected.includes(settings[key])
      : expected === "*"
        ? settings[key] !== undefined && settings[key] !== null && settings[key] !== ""
        : settings[key] === expected,
  );
}
