import { create } from "zustand";
import { api, ApiError } from "../lib/api";
import { applyChanges, marks, type Change, type Mark, type Proposal } from "../lib/copilot";
import type { FlowSpec, Issue } from "../lib/types";
import { useFlow } from "./flow";
import { useUi } from "./ui";

interface CopilotState {
  request: string;
  busy: boolean;
  error: string | null;
  proposal: Proposal | null;
  /** The changes and test cases the person keeps (all of them at first). */
  kept: string[];
  keptCases: number[];
  /** The flow with the kept changes, shown on the canvas while a proposal is open. */
  preview: FlowSpec | null;
  marks: Record<string, Mark>;
  setRequest: (request: string) => void;
  ask: (request: string, issue?: Issue) => Promise<void>;
  toggle: (id: string) => void;
  toggleCase: (index: number) => void;
  accept: () => Promise<void>;
  discard: () => void;
}

function shown(proposal: Proposal | null, kept: string[]) {
  const spec = useFlow.getState().spec;
  if (!proposal?.ok || !spec) return { preview: null, marks: {} };
  const changes = proposal.changes.filter((c) => kept.includes(c.id));
  return { preview: applyChanges(spec, changes), marks: marks(changes) };
}

export const useCopilot = create<CopilotState>()((set, get) => ({
  request: "",
  busy: false,
  error: null,
  proposal: null,
  kept: [],
  keptCases: [],
  preview: null,
  marks: {},
  setRequest: (request) => set({ request }),
  ask: async (request, issue) => {
    const spec = useFlow.getState().spec;
    useUi.getState().setRightTab("copilot");
    set({ busy: true, error: null, proposal: null, preview: null, marks: {} });
    try {
      const proposal = await api.copilot({ request, spec, issue });
      const kept = proposal.changes.map((c: Change) => c.id);
      set({ proposal, kept, keptCases: proposal.test_cases.map((_, i) => i), ...shown(proposal, kept) });
    } catch (err) {
      set({ error: err instanceof ApiError ? err.message : String(err) });
    } finally {
      set({ busy: false });
    }
  },
  toggle: (id) => {
    const { proposal, kept } = get();
    const next = kept.includes(id) ? kept.filter((k) => k !== id) : [...kept, id];
    set({ kept: next, ...shown(proposal, next) });
  },
  toggleCase: (index) => {
    const { keptCases } = get();
    set({ keptCases: keptCases.includes(index) ? keptCases.filter((i) => i !== index) : [...keptCases, index] });
  },
  accept: async () => {
    const { proposal, kept, keptCases } = get();
    if (!proposal?.ok) return;
    const changes = proposal.changes.filter((c) => kept.includes(c.id));
    // One undo step for the whole proposal.
    if (changes.length) useFlow.getState().apply((s) => applyChanges(s, changes));
    const flowId = useFlow.getState().flowId;
    const cases = proposal.test_cases.filter((_, i) => keptCases.includes(i));
    set({ proposal: null, preview: null, marks: {}, kept: [], keptCases: [], request: "" });
    if (flowId && cases.length) {
      try {
        await api.addTestCases(flowId, cases);
      } catch (err) {
        set({ error: err instanceof ApiError ? err.message : String(err) });
      }
    }
  },
  discard: () => set({ proposal: null, preview: null, marks: {}, kept: [], keptCases: [], error: null }),
}));

/** Read the flow the canvas shows: the copilot's preview while a proposal is open, else the flow. */
export function useShown<T>(pick: (spec: FlowSpec) => T): T | undefined {
  const previewing = useCopilot((s) => s.preview != null);
  const fromPreview = useCopilot((s) => (s.preview ? pick(s.preview) : undefined));
  const fromFlow = useFlow((s) => (s.spec && !previewing ? pick(s.spec) : undefined));
  return previewing ? fromPreview : fromFlow;
}

// Keep the preview current if the flow changes while a proposal is open (an edit in the
// inspector, an undo).
useFlow.subscribe((state, prev) => {
  if (state.spec === prev.spec) return;
  const { proposal, kept } = useCopilot.getState();
  if (proposal?.ok) useCopilot.setState(shown(proposal, kept));
});
