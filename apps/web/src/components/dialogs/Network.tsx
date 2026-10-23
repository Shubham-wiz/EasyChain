import { Check, Globe, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { NetworkSettings } from "../../lib/types";
import { cn } from "../../lib/utils";
import { Button, Textarea } from "../ui";

const MODES: { id: NetworkSettings["mode"]; label: string; help: string }[] = [
  { id: "public", label: "Public sites, and internal addresses on the list", help: "The usual choice for a shared server." },
  { id: "allow-list", label: "Only the sites on the list", help: "The strictest: nothing else can be reached." },
  { id: "open", label: "Anything", help: "Only for a server nobody else can reach, such as your own computer." },
];

/** Which addresses Web request steps, Knowledge Base pages, OpenAPI documents and MCP servers may reach. */
export function NetworkSection() {
  const [cfg, setCfg] = useState<NetworkSettings | null>(null);
  const [allow, setAllow] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api
      .network()
      .then((n) => {
        setCfg(n);
        setAllow(n.allow.join("\n"));
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  if (!cfg) return error ? <p className="text-xs text-danger">{error}</p> : <Loader2 size={16} className="animate-spin text-muted" />;
  const fixed = cfg.source === "environment";
  return (
    <section className="space-y-3" aria-label="Network">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold">
        <Globe size={14} /> Network
      </h3>
      <p className="text-xs text-muted">
        What Easy Chain may reach when a Web request step runs, or when it reads a Knowledge Base page, an OpenAPI document or an MCP server. Internal addresses (this
        machine, your network, the cloud's metadata service) are what a shared server has to protect. Calls to AI model providers aren't affected.
      </p>
      {fixed && (
        <p className="rounded-lg border border-border bg-surface-2 px-3 py-2 text-xs text-muted" role="note">
          The server's environment sets this (<span className="font-mono">EASYCHAIN_EGRESS</span>), so it can't be changed here.
        </p>
      )}
      <fieldset className="space-y-1.5" disabled={fixed}>
        <legend className="sr-only">What may be reached</legend>
        {MODES.map((m) => (
          <label key={m.id} className={cn("flex cursor-pointer gap-2 rounded-lg border p-2.5 text-sm", cfg.mode === m.id ? "border-accent bg-accent-soft/40" : "border-border")}>
            <input type="radio" name="egress-mode" value={m.id} checked={cfg.mode === m.id} onChange={() => setCfg({ ...cfg, mode: m.id })} className="mt-1" />
            <span>
              <span className="block font-medium">{m.label}</span>
              <span className="block text-xs text-muted">{m.help}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <div className="space-y-1">
        <label htmlFor="egress-allow" className="text-xs font-medium">
          The allow-list (one a line)
        </label>
        <Textarea
          id="egress-allow"
          rows={5}
          className="font-mono text-xs"
          placeholder={"docs.example.com\n*.intranet.example.com\n10.0.0.0/8"}
          value={allow}
          disabled={fixed}
          onChange={(e) => setAllow(e.target.value)}
        />
        <p className="text-[11px] text-faint">A host name, a name ending (*.example.com), an address (10.1.2.3) or a network (10.0.0.0/8). A name on the list is allowed wherever it points.</p>
      </div>
      {error && (
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      )}
      <Button
        variant="primary"
        size="sm"
        disabled={fixed || state === "saving"}
        onClick={async () => {
          setState("saving");
          try {
            const saved = await api.saveNetwork({ mode: cfg.mode, allow: allow.split("\n").map((a) => a.trim()).filter(Boolean) });
            setCfg(saved);
            setAllow(saved.allow.join("\n"));
            setError(null);
            setState("saved");
          } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
            setState("idle");
          }
        }}
      >
        {state === "saved" ? <Check size={13} /> : null} {state === "saved" ? "Saved" : "Save network settings"}
      </Button>
    </section>
  );
}
