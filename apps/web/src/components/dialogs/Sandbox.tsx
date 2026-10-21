// Settings → Sandbox: where Code steps run, and with what limits.

import { CheckCircle2, CircleAlert, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { SandboxSettings, SandboxStatus } from "../../lib/types";
import { useUi } from "../../state/ui";
import { Badge, Button, Field, Input, Select } from "../ui";

const MODES: { value: SandboxSettings["mode"]; label: string; help: string }[] = [
  { value: "auto", label: "Use Docker when it's there", help: "Without Docker, Code steps run in Easy Chain's own process and the checks say so." },
  { value: "docker", label: "Always (Code steps fail without Docker)", help: "For servers: code never runs outside a sandbox." },
  { value: "off", label: "Off: run Code steps in Easy Chain's process", help: "Only for code you trust completely, as exported code runs." },
];

function StatusLine({ status }: { status: SandboxStatus }) {
  if (status.effective === "docker")
    return (
      <p className="flex items-center gap-1.5 text-sm text-ok" data-testid="sandbox-status">
        <CheckCircle2 size={14} /> Code steps run in a sandbox (Docker {status.version}
        {status.gvisor ? ", with gVisor" : ""}).
      </p>
    );
  return (
    <div className="space-y-1" data-testid="sandbox-status">
      <p className={`flex items-center gap-1.5 text-sm ${status.effective === "unavailable" ? "text-danger" : "text-warn"}`}>
        <CircleAlert size={14} />
        {status.mode === "off"
          ? "The sandbox is off: Code steps run in Easy Chain's own process."
          : status.effective === "unavailable"
            ? "Code steps can't run: they must use the sandbox, and Docker isn't available."
            : "Code steps run in Easy Chain's own process, because Docker isn't available."}
      </p>
      {status.reason && status.mode !== "off" && <p className="text-xs text-muted">{status.reason}</p>}
    </div>
  );
}

export function SandboxSection() {
  const mode = useUi((s) => s.mode);
  const [settings, setSettings] = useState<SandboxSettings | null>(null);
  const [status, setStatus] = useState<SandboxStatus | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<"saving" | "checking" | "preparing" | null>(null);

  const check = (refresh = false) => {
    setBusy("checking");
    api
      .sandboxStatus(refresh)
      .then(setStatus)
      .catch(() => setStatus(null))
      .finally(() => setBusy(null));
  };
  useEffect(() => {
    api.sandboxSettings().then(setSettings).catch(() => setSettings(null));
    check();
  }, []);
  if (!settings) return <p className="text-sm text-muted">Loading…</p>;
  const set = (patch: Partial<SandboxSettings>) => setSettings({ ...settings, ...patch });
  const save = async () => {
    setBusy("saving");
    try {
      setSettings(await api.saveSandboxSettings(settings));
      setMessage({ ok: true, text: "Saved." });
      check();
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  };
  const prepare = async () => {
    setBusy("preparing");
    try {
      const result = await api.prepareSandbox();
      setMessage({ ok: result.ok, text: result.message });
      check(true);
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  };
  const chosen = MODES.find((m) => m.value === settings.mode);
  return (
    <section className="space-y-4" aria-label="Sandbox" data-testid="sandbox-settings">
      <div className="space-y-1">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold">
          <ShieldCheck size={14} /> Code step sandbox
        </h3>
        <p className="text-xs leading-relaxed text-muted">
          Code steps (and Code tools of agents) run in a throwaway container: no network, no access to this computer's files or secrets, and
          limits on memory, time and processes. It needs Docker (Docker Desktop on Windows and macOS). Exported code runs Code steps in its own
          process.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border p-3">
        {status ? <StatusLine status={status} /> : <p className="text-sm text-muted">Checking Docker…</p>}
        <div className="ml-auto flex gap-1.5">
          <Button size="sm" variant="outline" onClick={() => check(true)} disabled={busy !== null}>
            {busy === "checking" ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} Check again
          </Button>
          {status?.available && (
            <Button size="sm" variant="outline" onClick={prepare} disabled={busy !== null} data-testid="sandbox-prepare">
              {busy === "preparing" ? <Loader2 size={12} className="animate-spin" /> : null} Prepare the sandbox
            </Button>
          )}
        </div>
      </div>
      <Field label="Run Code steps in the sandbox" htmlFor="sandbox-mode" help={chosen?.help}>
        <Select id="sandbox-mode" value={settings.mode} onChange={(e) => set({ mode: e.target.value as SandboxSettings["mode"] })}>
          {MODES.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </Select>
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Memory (MB)" htmlFor="sandbox-memory">
          <Input id="sandbox-memory" type="number" min={32} value={settings.memory_mb} onChange={(e) => set({ memory_mb: Number(e.target.value) })} />
        </Field>
        <Field label="Time limit (seconds)" htmlFor="sandbox-timeout" help="A step's own time limit applies when it is shorter.">
          <Input id="sandbox-timeout" type="number" min={1} value={settings.timeout} onChange={(e) => set({ timeout: Number(e.target.value) })} />
        </Field>
        {mode === "pro" && (
          <>
            <Field label="CPUs" htmlFor="sandbox-cpus">
              <Input id="sandbox-cpus" type="number" step={0.5} min={0.1} value={settings.cpus} onChange={(e) => set({ cpus: Number(e.target.value) })} />
            </Field>
            <Field label="Most processes" htmlFor="sandbox-pids">
              <Input id="sandbox-pids" type="number" min={8} value={settings.pids} onChange={(e) => set({ pids: Number(e.target.value) })} />
            </Field>
            <Field label="Image" htmlFor="sandbox-image" help="A Linux image with Python 3. Packages a step lists are installed on top of it.">
              <Input id="sandbox-image" className="font-mono text-[12px]" value={settings.image} onChange={(e) => set({ image: e.target.value })} />
            </Field>
            <Field label="Container runtime" htmlFor="sandbox-runtime" help="gVisor (runsc) also keeps this computer's kernel out of the code's reach.">
              <Select id="sandbox-runtime" value={settings.runtime} onChange={(e) => set({ runtime: e.target.value as SandboxSettings["runtime"] })}>
                <option value="auto">gVisor when Docker has it</option>
                <option value="runsc">Always gVisor (runsc)</option>
                <option value="runc">Docker's default (runc)</option>
              </Select>
            </Field>
          </>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Button variant="primary" onClick={save} disabled={busy !== null}>
          Save sandbox settings
        </Button>
        <span role="status">{message && <Badge tone={message.ok ? "ok" : "danger"}>{message.text}</Badge>}</span>
      </div>
    </section>
  );
}
