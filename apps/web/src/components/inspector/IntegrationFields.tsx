// Inspector controls for Knowledge Bases, secrets, SQL and MCP tools.

import { ExternalLink } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../../lib/api";
import { updateSettings } from "../../lib/spec";
import type { KnowledgeBase, McpSettings, McpTool, SecretInfo } from "../../lib/types";
import { useFlow } from "../../state/flow";
import { useUi } from "../../state/ui";
import { Button, Input, Select, Textarea } from "../ui";
import type { FieldProps } from "./fields";

/** Pick a Knowledge Base; the step also records the embedding model it was built with. */
export function KnowledgeBasePicker({ value, id, stepId }: FieldProps) {
  const apply = useFlow((s) => s.apply);
  const [bases, setBases] = useState<KnowledgeBase[] | null>(null);
  useEffect(() => {
    api.knowledgeBases().then(setBases).catch(() => setBases([]));
  }, []);
  const current = bases?.find((b) => b.id === value);
  return (
    <div className="space-y-1.5">
      <div className="flex gap-1.5">
        <Select
          id={id}
          value={value ?? ""}
          onChange={(e) => {
            const base = bases?.find((b) => b.id === e.target.value);
            if (!stepId) return;
            apply((s) =>
              updateSettings(s, stepId, { knowledge_base: e.target.value, ...(base ? { embedding_model: base.embedding_model } : {}) }),
            );
          }}
        >
          <option value="">{bases ? (bases.length ? "Pick a Knowledge Base" : "No Knowledge Bases yet") : "Loading…"}</option>
          {(bases ?? []).map((b) => (
            <option key={b.id} value={b.id}>
              {b.name} ({typeof b.documents === "number" ? b.documents : 0} documents)
            </option>
          ))}
          {value && bases && !current && <option value={value}>{value} (not on this server)</option>}
        </Select>
        <Button variant="outline" size="sm" onClick={() => (window.location.hash = value ? `#/knowledge/${value}` : "#/knowledge")}>
          <ExternalLink size={12} /> {value ? "Open" : "Make one"}
        </Button>
      </div>
      {current && <p className="text-[11px] text-faint">Built with {current.embedding_label ?? current.embedding_model}.</p>}
    </div>
  );
}

/** Pick a secret (Settings → API keys) by name. */
export function SecretPicker({ value, onChange, id }: FieldProps) {
  const [secrets, setSecrets] = useState<SecretInfo[]>([]);
  useEffect(() => {
    api.secrets().then(setSecrets).catch(() => setSecrets([]));
  }, []);
  const listId = `${id}-secrets`;
  return (
    <>
      <Input id={id} list={listId} className="font-mono text-[13px]" value={value ?? ""} placeholder="MY_ENDPOINT_KEY" onChange={(e) => onChange(e.target.value.trim() || null)} />
      <datalist id={listId}>
        {secrets.map((s) => (
          <option key={s.name} value={s.name} />
        ))}
      </datalist>
    </>
  );
}

const SQL_VAR = /\{([a-z][a-z0-9_]*)\}/g;

/** SQL with {field} values (sent as parameters). */
export function SqlEditor({ value, onChange, id, fields }: FieldProps) {
  const text = String(value ?? "");
  const used = useMemo(() => Array.from(new Set(Array.from(text.matchAll(SQL_VAR), (m) => m[1]))), [text]);
  const known = new Set(fields.map((f) => f.name));
  return (
    <div className="space-y-1">
      <Textarea
        id={id}
        rows={Math.min(10, Math.max(3, text.split("\n").length + 1))}
        className="font-mono text-[12.5px]"
        value={text}
        spellCheck={false}
        placeholder="SELECT status, total FROM orders WHERE id = {order_id}"
        onChange={(e) => onChange(e.target.value)}
      />
      {used.length > 0 && (
        <p className="text-[11px] text-muted">
          Sent as parameters:{" "}
          {used.map((name) => (
            <span key={name} className={`mr-1 rounded px-1 font-mono ${known.has(name) ? "bg-accent-soft text-accent" : "bg-warn-soft text-warn"}`}>
              {name}
            </span>
          ))}
        </p>
      )}
    </div>
  );
}

function useMcpSettings() {
  const [settings, setSettings] = useState<McpSettings | null>(null);
  useEffect(() => {
    api.mcpSettings().then(setSettings).catch(() => setSettings({ servers: [], allowed_commands: [] }));
  }, []);
  return settings;
}

export function McpServerPicker({ value, onChange, id }: FieldProps) {
  const settings = useMcpSettings();
  const openSettings = useUi((s) => s.openSettings);
  return (
    <div className="flex gap-1.5">
      <Select id={id} value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        <option value="">{settings ? (settings.servers.length ? "Pick a server" : "No MCP servers yet") : "Loading…"}</option>
        {(settings?.servers ?? []).map((s) => (
          <option key={s.id} value={s.id}>
            {s.name || s.id}
          </option>
        ))}
      </Select>
      <Button variant="outline" size="sm" onClick={() => openSettings({ tab: "mcp" })}>
        Servers
      </Button>
    </div>
  );
}

/** Pick one of a server's tools; picking fills in its arguments. */
export function McpToolPicker({ value, id, settings, stepId }: FieldProps) {
  const apply = useFlow((s) => s.apply);
  const [tools, setTools] = useState<McpTool[] | string | null>(null);
  const server = settings.server as string | undefined;
  useEffect(() => {
    if (!server) return setTools(null);
    setTools("loading");
    api
      .mcpTools({ server_id: server })
      .then((r) => setTools(r.tools))
      .catch((err) => setTools(err instanceof ApiError ? err.message : String(err)));
  }, [server]);
  if (!server) return <p className="text-xs text-faint">Pick the server first.</p>;
  if (tools === "loading") return <p className="text-xs text-faint">Asking the server for its tools…</p>;
  if (typeof tools === "string") return <p className="text-xs text-danger">{tools}</p>;
  const current = Array.isArray(tools) ? tools.find((t) => t.name === value) : undefined;
  return (
    <div className="space-y-1">
      <Select
        id={id}
        value={value ?? ""}
        onChange={(e) => {
          const tool = Array.isArray(tools) ? tools.find((t) => t.name === e.target.value) : undefined;
          if (!stepId) return;
          const args = Object.fromEntries(Object.keys(tool?.args ?? {}).map((k) => [k, (settings.arguments ?? {})[k] ?? ""]));
          apply((s) => updateSettings(s, stepId, { tool: e.target.value, arguments: args }));
        }}
      >
        <option value="">Pick a tool</option>
        {(Array.isArray(tools) ? tools : []).map((t) => (
          <option key={t.name} value={t.name}>
            {t.name}
          </option>
        ))}
      </Select>
      {current?.description && <p className="text-[11px] text-muted">{current.description}</p>}
    </div>
  );
}
