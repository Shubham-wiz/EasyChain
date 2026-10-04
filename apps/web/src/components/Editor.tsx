import { ReactFlowProvider } from "@xyflow/react";
import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { copySteps, pasteSteps, type Clipboard } from "../lib/spec";
import type { FlowSpec } from "../lib/types";
import { useCheck } from "../state/check";
import { redo, undo, useFlow } from "../state/flow";
import { attachRun, startRun, useRun } from "../state/run";
import { useUi } from "../state/ui";
import { Canvas } from "./canvas/Canvas";
import { ExportDialog } from "./dialogs/ExportDialog";
import { ImportApiDialog } from "./dialogs/ImportApiDialog";
import { SettingsDialog } from "./dialogs/SettingsDialog";
import { TriggersDialog } from "./dialogs/TriggersDialog";
import { Inspector } from "./inspector/Inspector";
import { RunPanel } from "./run/RunPanel";
import { StepLibrary } from "./StepLibrary";
import { TopBar } from "./TopBar";
import { Button, Tabs, TabsContent, TabsList, TabsTrigger } from "./ui";

const CLIPBOARD_KEY = "easychain.clipboard";

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  return el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || !!el.closest(".monaco-editor");
}

/** Save, check and compile in the background as the flow changes. */
function useBackgroundSync() {
  const spec = useFlow((s) => s.spec);
  const flowId = useFlow((s) => s.flowId);
  const saveState = useFlow((s) => s.saveState);
  // Edits waiting for the save timer.
  const pending = useRef<{ flowId: string; spec: FlowSpec } | null>(null);

  useEffect(() => {
    if (!spec || !flowId || saveState !== "unsaved") {
      pending.current = null;
      return;
    }
    pending.current = { flowId, spec };
    const t = setTimeout(async () => {
      pending.current = null;
      useFlow.getState().setSaveState("saving");
      try {
        await api.saveFlow(flowId, spec);
        if (useFlow.getState().spec === spec) useFlow.getState().setSaveState("saved");
        else useFlow.getState().setSaveState("unsaved");
      } catch (err) {
        useFlow.getState().setSaveState("error", err instanceof Error ? err.message : String(err));
      }
    }, 700);
    return () => clearTimeout(t);
  }, [spec, flowId, saveState]);

  // Leaving the flow (another flow, another page, closing the tab) saves pending edits now
  // instead of dropping them with the timer.
  useEffect(() => {
    const flush = (keepalive: boolean) => {
      const waiting = pending.current;
      if (!waiting) return;
      pending.current = null;
      api.saveFlow(waiting.flowId, waiting.spec, { keepalive }).catch(() => {
        /* the page is going away; nothing left to tell */
      });
    };
    const onPageHide = () => flush(true);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.removeEventListener("pagehide", onPageHide);
      flush(false);
    };
  }, [flowId]);

  useEffect(() => {
    if (!spec) return;
    let cancelled = false;
    const t = setTimeout(async () => {
      useCheck.getState().set({ checking: true });
      try {
        const result = await api.check(spec, flowId);
        if (!cancelled) useCheck.getState().set({ issues: result.issues, analysis: result.analysis, checking: false });
      } catch {
        if (!cancelled) useCheck.getState().set({ checking: false });
      }
    }, 250);
    const c = setTimeout(async () => {
      try {
        const compiled = await api.compile(spec, flowId);
        if (!cancelled) useCheck.getState().set({ compiled });
      } catch {
        /* shown via checks */
      }
    }, 600);
    return () => {
      cancelled = true;
      clearTimeout(t);
      clearTimeout(c);
    };
  }, [spec, flowId]);
}

/**
 * React Flow tracks pressed keys for its own shortcuts (Delete, multi-select). After our
 * modifier shortcuts change the canvas it can be left thinking a key is still down, which
 * blocks Delete; a window blur event makes it forget them.
 */
function resetCanvasKeys() {
  setTimeout(() => window.dispatchEvent(new Event("blur")), 0);
}

function useShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key === "Enter") {
        e.preventDefault();
        useUi.getState().setRightTab("run");
        window.dispatchEvent(new Event("easychain:run"));
        return;
      }
      if (isTyping(e.target)) return;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
        resetCanvasKeys();
      } else if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        redo();
        resetCanvasKeys();
      } else if (mod && e.key.toLowerCase() === "c") {
        const { spec } = useFlow.getState();
        const ids = useUi.getState().selected;
        if (spec && ids.length) {
          try {
            localStorage.setItem(CLIPBOARD_KEY, JSON.stringify(copySteps(spec, ids)));
          } catch {
            /* storage unavailable */
          }
        }
      } else if (mod && e.key.toLowerCase() === "v") {
        let clip: Clipboard | null = null;
        try {
          clip = JSON.parse(localStorage.getItem(CLIPBOARD_KEY) ?? "null");
        } catch {
          clip = null;
        }
        if (!clip?.steps?.length) return;
        e.preventDefault();
        let pasted: string[] = [];
        useFlow.getState().apply((s) => {
          const result = pasteSteps(s, clip!);
          pasted = result.ids;
          return result.spec;
        });
        useUi.getState().select(pasted);
        resetCanvasKeys();
      } else if (e.key === "/") {
        e.preventDefault();
        document.getElementById("library-search")?.focus();
      } else if (e.key === "Escape") {
        useUi.getState().select([]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

export function Editor({ flowId, tryIt, onHome }: { flowId: string; tryIt: boolean; onHome: () => void }) {
  const spec = useFlow((s) => s.spec);
  const loadedId = useFlow((s) => s.flowId);
  const rightTab = useUi((s) => s.rightTab);
  const setRightTab = useUi((s) => s.setRightTab);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    useRun.getState().newChat();
    useCheck.getState().set({ issues: [], analysis: null, compiled: null });
    useUi.getState().select([]);
    useUi.getState().setRightTab(tryIt ? "run" : "inspect");
    useUi.getState().loadBreakpoints(flowId);
    api
      .flow(flowId)
      .then(({ spec }) => {
        if (cancelled) return;
        useFlow.getState().load(flowId, spec);
        if (tryIt) void tryRun(spec);
        else void showActiveRun(flowId);
      })
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
    };
  }, [flowId, tryIt]);

  useBackgroundSync();
  useShortcuts();

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3">
        <p className="text-sm text-danger">{error}</p>
        <Button onClick={onHome}>Back to all flows</Button>
      </div>
    );
  }
  if (!spec || loadedId !== flowId) {
    return (
      <div className="flex h-full items-center justify-center text-muted">
        <Loader2 className="mr-2 animate-spin" size={18} /> Opening flow…
      </div>
    );
  }
  return (
    <ReactFlowProvider>
      <div className="flex h-full flex-col">
        <TopBar onHome={onHome} />
        <div className="flex min-h-0 flex-1">
          <StepLibrary />
          <main className="relative min-w-0 flex-1" aria-label="Canvas">
            <Canvas key={flowId} />
          </main>
          <aside className="flex w-[380px] shrink-0 flex-col border-l border-border bg-surface" aria-label="Inspector and run">
            <Tabs value={rightTab} onValueChange={(v) => setRightTab(v as "inspect" | "run")} className="flex min-h-0 flex-1 flex-col">
              <TabsList>
                <TabsTrigger value="inspect">Inspect</TabsTrigger>
                <TabsTrigger value="run">Run</TabsTrigger>
              </TabsList>
              <TabsContent value="inspect" className="min-h-0 flex-1 overflow-hidden">
                <div className="scroll-thin h-full overflow-y-auto">
                  <Inspector />
                </div>
              </TabsContent>
              <TabsContent value="run" className="min-h-0 flex-1 overflow-hidden" forceMount hidden={rightTab !== "run"}>
                <RunPanel />
              </TabsContent>
            </Tabs>
          </aside>
        </div>
        <ExportDialog />
        <SettingsDialog />
        <TriggersDialog />
        <ImportApiDialog />
      </div>
    </ReactFlowProvider>
  );
}

/** A run of this flow that is still going or waiting for someone: show it (it survives reloads). */
async function showActiveRun(flowId: string) {
  try {
    const runs = await api.runs(flowId, { status: "running,queued,paused" });
    const active = runs.find((r) => r.status === "running" || r.status === "queued") ?? runs[0];
    if (!active || useFlow.getState().flowId !== flowId) return;
    if (active.status === "paused" && Date.now() / 1000 - active.started > 3600) return; // old pauses live in the Inbox
    await attachRun(active.run_id);
  } catch {
    /* nothing to show */
  }
}

/** "Try it" from the gallery: run on the template's sample data (stand-in AI if a key is missing). */
async function tryRun(spec: FlowSpec) {
  const input = spec.steps.find((s) => s.type === "input");
  const providers = await api.catalog().then((c) => c.providers).catch(() => []);
  const missingKey = spec.steps.some((s) => {
    const model = s.settings?.model as string | undefined;
    if (!model || (s.type === "decision" && s.settings.mode !== "ai")) return false;
    const p = providers.find((x) => x.id === model.split(":")[0]);
    return p ? !p.key_set : false;
  });
  if (missingKey) useUi.getState().setStandIn(true);
  if (input?.settings.mode === "chat") {
    await startRun({}, { chatMessage: "Hello! What can you help me with?" });
    return;
  }
  const inputs = Object.fromEntries(
    ((input?.settings.fields ?? []) as { name: string; example: unknown }[]).filter((f) => f.example != null).map((f) => [f.name, f.example]),
  );
  await startRun(inputs);
}
