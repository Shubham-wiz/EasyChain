import { create } from "zustand";
import { temporal } from "zundo";
import type { FlowSpec } from "../lib/types";

let lastMerge: { key: string | null; at: number } = { key: null, at: 0 };

export type SaveState = "saved" | "saving" | "unsaved" | "error";

interface FlowState {
  flowId: string | null;
  spec: FlowSpec | null;
  saveState: SaveState;
  saveError: string | null;
  load: (flowId: string, spec: FlowSpec) => void;
  /**
   * Apply an edit as one undo step. Edits that pass the same `mergeKey` in quick
   * succession (typing in one field) share a single undo step.
   */
  apply: (edit: (spec: FlowSpec) => FlowSpec, mergeKey?: string) => void;
  setSaveState: (state: SaveState, error?: string | null) => void;
  close: () => void;
}

export const useFlow = create<FlowState>()(
  temporal(
    (set, get) => ({
      flowId: null,
      spec: null,
      saveState: "saved",
      saveError: null,
      load: (flowId, spec) => {
        lastMerge = { key: null, at: 0 };
        set({ flowId, spec, saveState: "saved", saveError: null });
        useFlow.temporal.getState().clear();
      },
      apply: (edit, mergeKey) => {
        const spec = get().spec;
        if (!spec) return;
        const next = edit(spec);
        if (next === spec) return;
        const now = Date.now();
        const merge = !!mergeKey && mergeKey === lastMerge.key && now - lastMerge.at < 1500;
        lastMerge = { key: mergeKey ?? null, at: now };
        const temporalStore = useFlow.temporal.getState();
        if (merge) temporalStore.pause();
        set({ spec: next, saveState: "unsaved" });
        if (merge) temporalStore.resume();
      },
      setSaveState: (saveState, saveError = null) => set({ saveState, saveError }),
      close: () => set({ flowId: null, spec: null }),
    }),
    {
      limit: 200,
      partialize: (state) => ({ spec: state.spec }),
      equality: (a, b) => a.spec === b.spec,
    },
  ),
);

export function undo() {
  lastMerge = { key: null, at: 0 };
  useFlow.temporal.getState().undo();
  useFlow.setState({ saveState: "unsaved" });
}

export function redo() {
  lastMerge = { key: null, at: 0 };
  useFlow.temporal.getState().redo();
  useFlow.setState({ saveState: "unsaved" });
}
