// Import an API from its OpenAPI (Swagger) description: operations become Web request steps,
// as Actions or as an agent's tools, with typed, described Flow Data fields.

import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import { addImportedSteps } from "../../lib/spec";
import type { OpenApiInspection } from "../../lib/types";
import { useFlow } from "../../state/flow";
import { useUi } from "../../state/ui";
import { Badge, Button, Dialog, Field, Input, Select, Textarea } from "../ui";

const METHOD_TONE: Record<string, "ok" | "accent" | "warn" | "danger" | "neutral"> = {
  GET: "ok",
  POST: "accent",
  PUT: "warn",
  PATCH: "warn",
  DELETE: "danger",
};

export function ImportApiDialog() {
  const open = useUi((s) => s.importOpen);
  const setOpen = useUi((s) => s.setImportOpen);
  const select = useUi((s) => s.select);
  const spec = useFlow((s) => s.spec);
  const apply = useFlow((s) => s.apply);
  const [source, setSource] = useState("");
  const [found, setFound] = useState<OpenApiInspection | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [server, setServer] = useState("");
  const [authHeader, setAuthHeader] = useState("");
  const [authSecret, setAuthSecret] = useState("");
  const [agent, setAgent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) {
      setFound(null);
      setPicked([]);
      setError(null);
    }
  }, [open]);

  const agents = (spec?.steps ?? []).filter((s) => s.type === "agent");
  const inspect = async () => {
    setBusy(true);
    try {
      const result = await api.inspectOpenApi(source.trim());
      setFound(result);
      setServer(result.server);
      setPicked([]);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  const add = async () => {
    if (!spec) return;
    setBusy(true);
    try {
      const made = await api.openApiSteps({
        source: source.trim(),
        operations: picked,
        server: server || undefined,
        auth_header: authHeader || undefined,
        auth_secret: authSecret || undefined,
        taken: spec.steps.map((s) => s.id),
      });
      apply((s) => addImportedSteps(s, made.steps, made.data, agent || null));
      select(made.steps.map((s) => s.id));
      setOpen(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen} title="Import an API" description="Paste the address of an OpenAPI (Swagger) description, or the description itself.">
      <div className="space-y-4" data-testid="import-api">
        <div className="space-y-2">
          <Textarea
            aria-label="OpenAPI address or text"
            rows={found ? 1 : 4}
            className="font-mono text-[12px]"
            placeholder={"https://petstore3.swagger.io/api/v3/openapi.json\n\nor paste the JSON / YAML"}
            value={source}
            onChange={(e) => setSource(e.target.value)}
          />
          <Button variant={found ? "outline" : "primary"} size="sm" disabled={!source.trim() || busy} onClick={inspect}>
            {busy && !found ? <Loader2 size={13} className="animate-spin" /> : null} Read it
          </Button>
        </div>
        {error && <p className="text-xs text-danger">{error}</p>}
        {found && (
          <>
            <p className="text-sm">
              <strong>{found.title || "This API"}</strong> has {found.operations.length} operations. Pick the ones you need.
            </p>
            <ul className="max-h-64 space-y-1 overflow-y-auto rounded-lg border border-border p-1.5" aria-label="Operations">
              {found.operations.map((op) => (
                <li key={op.id}>
                  <label className="flex cursor-pointer items-start gap-2 rounded px-1.5 py-1 text-xs hover:bg-surface-2">
                    <input
                      type="checkbox"
                      className="mt-0.5 accent-[var(--accent)]"
                      checked={picked.includes(op.id)}
                      onChange={(e) => setPicked(e.target.checked ? [...picked, op.id] : picked.filter((p) => p !== op.id))}
                    />
                    <Badge tone={METHOD_TONE[op.method] ?? "neutral"}>{op.method}</Badge>
                    <span className="min-w-0 flex-1">
                      <span className="font-mono">{op.path}</span>
                      {op.summary && <span className="block text-muted">{op.summary}</span>}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Server" htmlFor="import-server">
                <Input id="import-server" className="font-mono text-[12px]" value={server} onChange={(e) => setServer(e.target.value)} />
              </Field>
              <Field label="Add them" htmlFor="import-agent" help="As steps of the flow, or as tools an agent can use.">
                <Select id="import-agent" value={agent} onChange={(e) => setAgent(e.target.value)}>
                  <option value="">As steps on the canvas</option>
                  {agents.map((a) => (
                    <option key={a.id} value={a.id}>
                      As tools of “{a.name || a.id}”
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Key header (optional)" htmlFor="import-auth-header" help="For example Authorization or X-API-Key.">
                <Input id="import-auth-header" value={authHeader} placeholder="Authorization" onChange={(e) => setAuthHeader(e.target.value)} />
              </Field>
              <Field label="From the secret" htmlFor="import-auth-secret" help="Its value is sent in that header (Settings → secrets).">
                <Input id="import-auth-secret" className="font-mono uppercase" value={authSecret} placeholder="PETSTORE_KEY" onChange={(e) => setAuthSecret(e.target.value.toUpperCase())} />
              </Field>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={!picked.length || busy} onClick={add} data-testid="import-api-add">
                {busy ? <Loader2 size={13} className="animate-spin" /> : null} Add {picked.length || ""} step{picked.length === 1 ? "" : "s"}
              </Button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}
