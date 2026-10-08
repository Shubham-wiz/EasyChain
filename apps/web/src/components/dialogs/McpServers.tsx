// Settings → MCP servers: connect servers over HTTP (or, in Pro mode, approved local commands).

import { Plug, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import { toIdent } from "../../lib/spec";
import type { McpServer, McpSettings, McpTool } from "../../lib/types";
import { useUi } from "../../state/ui";
import { Badge, Button, DraftInput, Input, SecretInput, Select, useRowKeys } from "../ui";

function blank(n: number): McpServer {
  return { id: `server_${n}`, name: "", transport: "http", url: "", headers: {}, command: "", args: [], env: {} };
}

function ServerCard({
  server,
  allowed,
  onChange,
  onRemove,
}: {
  server: McpServer;
  allowed: string[];
  onChange: (s: McpServer) => void;
  onRemove: () => void;
}) {
  const mode = useUi((s) => s.mode);
  const [tools, setTools] = useState<McpTool[] | string | null>(null);
  const set = (patch: Partial<McpServer>) => onChange({ ...server, ...patch });
  const words = server.command.trim().split(/\s+/);
  const command = words[0] ?? "";
  // A program on its own approves it with any arguments; an entry with arguments, that exact command line.
  const approved = server.transport !== "stdio" || allowed.some((a) => a === command || a.trim().split(/\s+/).join(" ") === words.join(" "));
  return (
    <div className="space-y-2 rounded-lg border border-border p-3" data-testid={`mcp-server-${server.id}`}>
      <div className="flex gap-1.5">
        <Input aria-label="Server name" placeholder="Name, e.g. Company docs" value={server.name} onChange={(e) => set({ name: e.target.value })} />
        <DraftInput
          aria-label="Server id"
          className="w-36 font-mono text-[12px]"
          value={server.id}
          clean={(text) => toIdent(text, "server")}
          onCommit={(id) => set({ id })}
        />
        <Button size="icon" variant="ghost" aria-label={`Remove ${server.name || server.id}`} onClick={onRemove}>
          <Trash2 size={14} />
        </Button>
      </div>
      <Select aria-label="How to reach it" value={server.transport} onChange={(e) => set({ transport: e.target.value as McpServer["transport"] })}>
        <option value="http">Over the web (streamable HTTP)</option>
        <option value="sse">Over the web (server-sent events, older servers)</option>
        {(mode === "pro" || server.transport === "stdio") && <option value="stdio">A program on this server (Pro)</option>}
      </Select>
      {server.transport === "stdio" ? (
        <>
          <Input aria-label="Command" className="font-mono text-[12px]" placeholder="npx -y @modelcontextprotocol/server-filesystem /data" value={server.command} onChange={(e) => set({ command: e.target.value })} />
          {!approved && <p className="text-xs text-warn">`{server.command.trim() || "?"}` isn't on the approved list below, so it won't run.</p>}
        </>
      ) : (
        <Input aria-label="Server URL" className="font-mono text-[12px]" placeholder="https://example.com/mcp" value={server.url} onChange={(e) => set({ url: e.target.value })} />
      )}
      {server.transport !== "stdio" && (
        <SecretInput
          aria-label="Authorization header"
          className="font-mono text-[12px]"
          placeholder="Authorization header, e.g. Bearer {secret:DOCS_TOKEN}"
          value={server.headers.Authorization ?? ""}
          onChange={(value) => set({ headers: value ? { ...server.headers, Authorization: value } : {} })}
        />
      )}
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            setTools("loading");
            api
              .mcpTools({ server })
              .then((r) => setTools(r.tools))
              .catch((err) => setTools(err instanceof ApiError ? err.message : String(err)));
          }}
        >
          <Plug size={12} /> Show its tools
        </Button>
        {tools === "loading" && <span className="text-xs text-faint">Connecting…</span>}
        {Array.isArray(tools) && <Badge tone="ok">{tools.length} tools</Badge>}
      </div>
      {typeof tools === "string" && tools !== "loading" && <p className="text-xs text-danger">{tools}</p>}
      {Array.isArray(tools) && (
        <ul className="space-y-0.5 text-xs" data-testid="mcp-tools-list">
          {tools.map((t) => (
            <li key={t.name}>
              <span className="font-mono">{t.name}</span>
              {t.description && <span className="text-muted"> · {t.description}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function McpSection() {
  const mode = useUi((s) => s.mode);
  const [settings, setSettings] = useState<McpSettings | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // A card keeps its key (and the tools it showed) when a card above it is removed.
  const rows = useRowKeys(settings?.servers.length ?? 0);
  useEffect(() => {
    api.mcpSettings().then(setSettings).catch(() => setSettings({ servers: [], allowed_commands: [] }));
  }, []);
  if (!settings) return <p className="text-sm text-muted">Loading…</p>;
  const save = async () => {
    try {
      const next = await api.saveMcpSettings(settings);
      setSettings(next);
      setError(null);
      setSaved("Saved.");
      setTimeout(() => setSaved(null), 2500);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };
  return (
    <section className="space-y-3" data-testid="mcp-settings">
      <p className="text-xs leading-relaxed text-muted">
        MCP servers give agents more tools: search your company docs, open tickets, read files. Agents and MCP tool steps can use any server you
        connect here.
      </p>
      {settings.servers.map((server, i) => (
        <ServerCard
          key={rows.keys[i]}
          server={server}
          allowed={settings.allowed_commands}
          onChange={(s) => setSettings({ ...settings, servers: settings.servers.map((x, j) => (j === i ? s : x)) })}
          onRemove={() => {
            rows.remove(i);
            setSettings({ ...settings, servers: settings.servers.filter((_, j) => j !== i) });
          }}
        />
      ))}
      <Button size="sm" variant="outline" onClick={() => setSettings({ ...settings, servers: [...settings.servers, blank(settings.servers.length + 1)] })}>
        <Plus size={13} /> Add a server
      </Button>
      <div className="space-y-1.5 rounded-lg border border-border p-3">
        <p className="text-sm font-medium">Approved local commands</p>
        <p className="text-xs text-muted">
          A server that runs as a program on this machine can do anything that program can, so only commands on this list may start. Edit it in
          Pro mode.
        </p>
        <p className="text-xs text-muted">
          A program on its own (<span className="font-mono">npx</span>, <span className="font-mono">uvx</span>,{" "}
          <span className="font-mono">python</span>, <span className="font-mono">node</span>) approves it with any arguments: that is,
          anything it can download or run. To approve one server only, write its whole command line; it must then match exactly.
        </p>
        <DraftInput
          aria-label="Approved commands"
          className="font-mono text-[12px]"
          disabled={mode !== "pro"}
          placeholder="npx -y @modelcontextprotocol/server-filesystem /data"
          value={settings.allowed_commands.join(", ")}
          onCommit={(text) =>
            setSettings({
              ...settings,
              allowed_commands: text
                .split(",")
                .map((x) => x.trim())
                .filter(Boolean),
            })
          }
        />
      </div>
      <div className="flex items-center gap-2">
        <Button variant="primary" onClick={save}>
          Save MCP servers
        </Button>
        {saved && <span className="text-xs text-ok">{saved}</span>}
        {error && <span className="text-xs text-danger">{error}</span>}
      </div>
    </section>
  );
}
