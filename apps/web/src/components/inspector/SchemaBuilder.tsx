// The structured output builder: the fields a model or agent must reply with.

import { Plus, Trash2 } from "lucide-react";
import { toIdent } from "../../lib/spec";
import { Button, Input, Select, Switch } from "../ui";
import type { FieldProps } from "./fields";

export interface SchemaField {
  name: string;
  type: "text" | "number" | "whole_number" | "yes_no" | "choice" | "list" | "object";
  description?: string;
  required?: boolean;
  options?: string[];
  items?: "text" | "number" | "whole_number" | "yes_no" | "object";
  fields?: SchemaField[];
}

export interface StructuredOutput {
  fields: SchemaField[];
  description?: string;
  spread?: boolean;
  retries?: number;
}

const TYPES: { value: SchemaField["type"]; label: string }[] = [
  { value: "text", label: "Text" },
  { value: "number", label: "Number" },
  { value: "whole_number", label: "Whole number" },
  { value: "yes_no", label: "Yes / no" },
  { value: "choice", label: "One of a list" },
  { value: "list", label: "List" },
  { value: "object", label: "Group of fields" },
];

const ITEMS: { value: NonNullable<SchemaField["items"]>; label: string }[] = [
  { value: "text", label: "of text" },
  { value: "number", label: "of numbers" },
  { value: "whole_number", label: "of whole numbers" },
  { value: "yes_no", label: "of yes / no" },
  { value: "object", label: "of groups" },
];

function newField(list: SchemaField[]): SchemaField {
  const taken = new Set(list.map((f) => f.name));
  let n = list.length + 1;
  while (taken.has(`field_${n}`)) n += 1;
  return { name: list.length ? `field_${n}` : "answer", type: "text", description: "", required: true };
}

function FieldsEditor({ list, onChange, depth }: { list: SchemaField[]; onChange: (list: SchemaField[]) => void; depth: number }) {
  const set = (i: number, patch: Partial<SchemaField>) => onChange(list.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  return (
    <div className="space-y-1.5">
      {list.map((f, i) => {
        const nested = f.type === "object" || (f.type === "list" && f.items === "object");
        return (
          <div key={i} className="space-y-1.5 rounded-lg border border-border bg-surface p-2" data-testid={`schema-field-${f.name}`}>
            <div className="flex gap-1.5">
              <Input
                aria-label="Field name"
                className="h-8 font-mono text-[12px]"
                defaultValue={f.name}
                onBlur={(e) => {
                  const clean = toIdent(e.target.value, "field");
                  e.target.value = clean;
                  if (clean !== f.name) set(i, { name: clean });
                }}
              />
              <Select
                aria-label="Kind of value"
                className="h-8 w-36 text-xs"
                value={f.type}
                onChange={(e) => {
                  const type = e.target.value as SchemaField["type"];
                  set(i, {
                    type,
                    options: type === "choice" ? (f.options?.length ? f.options : ["yes", "no"]) : f.options,
                    fields: type === "object" ? (f.fields?.length ? f.fields : [newField([])]) : f.fields,
                  });
                }}
              >
                {TYPES.filter((t) => depth < 2 || t.value !== "object").map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </Select>
              <Button size="icon-sm" variant="ghost" aria-label={`Remove ${f.name}`} onClick={() => onChange(list.filter((_, j) => j !== i))}>
                <Trash2 size={13} />
              </Button>
            </div>
            {f.type === "list" && (
              <Select
                aria-label="What each item is"
                className="h-7 text-xs"
                value={f.items ?? "text"}
                onChange={(e) => {
                  const items = e.target.value as SchemaField["items"];
                  set(i, { items, fields: items === "object" ? (f.fields?.length ? f.fields : [newField([])]) : f.fields });
                }}
              >
                {ITEMS.filter((t) => depth < 2 || t.value !== "object").map((t) => (
                  <option key={t.value} value={t.value}>
                    List {t.label}
                  </option>
                ))}
              </Select>
            )}
            {f.type === "choice" && (
              <Input
                aria-label="Choices"
                className="h-7 text-xs"
                placeholder="positive, negative, mixed"
                defaultValue={(f.options ?? []).join(", ")}
                onBlur={(e) => set(i, { options: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })}
              />
            )}
            <Input
              aria-label="Description for the model"
              className="h-7 text-xs"
              placeholder="What to put here, e.g. the main reason in a few words"
              value={f.description ?? ""}
              onChange={(e) => set(i, { description: e.target.value })}
            />
            <label className="flex items-center gap-2 text-[11px] text-muted">
              <input type="checkbox" className="accent-[var(--accent)]" checked={f.required ?? true} onChange={(e) => set(i, { required: e.target.checked })} />
              Always filled in
            </label>
            {nested && (
              <div className="border-l-2 border-accent/30 pl-2">
                <FieldsEditor list={f.fields ?? []} onChange={(fields) => set(i, { fields })} depth={depth + 1} />
              </div>
            )}
          </div>
        );
      })}
      <Button size="sm" variant="outline" onClick={() => onChange([...list, newField(list)])}>
        <Plus size={13} /> Add a field
      </Button>
    </div>
  );
}

export function SchemaBuilder({ value, onChange, field }: FieldProps) {
  const output: StructuredOutput | null = value ?? null;
  const set = (patch: Partial<StructuredOutput>) => onChange({ ...(output ?? { fields: [] }), ...patch });
  const isAgent = field.label.toLowerCase().includes("answer");
  return (
    <div className="space-y-2" data-testid="schema-builder">
      <div className="flex rounded-lg border border-border p-0.5 text-xs" role="radiogroup" aria-label={field.label}>
        {[
          { on: false, label: "Free text" },
          { on: true, label: "Fixed fields" },
        ].map((opt) => (
          <button
            key={opt.label}
            type="button"
            role="radio"
            aria-checked={!!output === opt.on}
            className={`flex-1 rounded-md px-2 py-1 font-medium ${!!output === opt.on ? "bg-accent text-accent-text" : "text-muted hover:bg-surface-2"}`}
            onClick={() => onChange(opt.on ? (output ?? { fields: [newField([])], spread: true, retries: 1 }) : null)}
          >
            {opt.label}
          </button>
        ))}
      </div>
      {output && (
        <>
          <FieldsEditor list={output.fields} onChange={(fields) => set({ fields })} depth={0} />
          <Input
            aria-label="What the reply is"
            className="h-8 text-xs"
            placeholder={isAgent ? "What the answer is, for the agent" : "What the reply is, for the model"}
            value={output.description ?? ""}
            onChange={(e) => set({ description: e.target.value })}
          />
          <label className="flex items-center gap-2 text-xs text-muted">
            <Switch checked={!!output.spread} onCheckedChange={(spread) => set({ spread })} label="Also save each field on its own" />
            Also save each field as its own Flow Data field
          </label>
          <label className="flex items-center gap-2 text-xs text-muted">
            <span className="flex-1">Ask again when a reply doesn't fit</span>
            <Input
              aria-label="Retries"
              type="number"
              min={0}
              max={5}
              className="h-7 w-16 text-xs"
              value={output.retries ?? 1}
              onChange={(e) => set({ retries: Number(e.target.value) })}
            />
          </label>
        </>
      )}
    </div>
  );
}
