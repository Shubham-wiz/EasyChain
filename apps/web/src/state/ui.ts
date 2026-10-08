import { create } from "zustand";

export type Mode = "beginner" | "pro";
export type SettingsTab = "keys" | "mcp" | "notifications";
type Theme = "light" | "dark";
export type RightTab = "inspect" | "run";

function stored<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T | null) ?? fallback;
  } catch {
    return fallback;
  }
}

function persist(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode */
  }
}

function forget(key: string) {
  try {
    localStorage.removeItem(key);
  } catch {
    /* private mode */
  }
}

const STAND_IN = "easychain.standIn";

/** The stand-in setting for a flow: its own (set by "Try it"), or else the one for every flow. */
function storedStandIn(flowId: string | null): boolean {
  const own = flowId ? stored<string>(`${STAND_IN}.${flowId}`, "") : "";
  return (own || stored<string>(STAND_IN, "false")) === "true";
}

export interface Breakpoints {
  before: string[];
  after: string[];
}

function storedBreakpoints(flowId: string): Breakpoints {
  try {
    const raw = JSON.parse(localStorage.getItem(`easychain.breakpoints.${flowId}`) ?? "null");
    if (raw && Array.isArray(raw.before) && Array.isArray(raw.after)) return raw;
  } catch {
    /* ignore */
  }
  return { before: [], after: [] };
}

interface UiState {
  mode: Mode;
  theme: Theme;
  selected: string[];
  rightTab: RightTab;
  settings: { open: boolean; provider?: string; secret?: string; tab?: SettingsTab };
  importOpen: boolean;
  exportOpen: boolean;
  problemsOpen: boolean;
  /** Use the stand-in AI for test runs of the open flow. */
  standIn: boolean;
  focusSetting: { step: string; key: string; at: number } | null;
  triggersOpen: boolean;
  /** Breakpoints for test runs of the open flow (kept per flow in this browser). */
  breakpoints: Breakpoints;
  /** The flow whose breakpoints and stand-in setting are loaded. */
  prefsFor: string | null;
  setMode: (mode: Mode) => void;
  toggleTheme: () => void;
  select: (ids: string[]) => void;
  setRightTab: (tab: RightTab) => void;
  openSettings: (focus?: { provider?: string; secret?: string; tab?: SettingsTab }) => void;
  setImportOpen: (open: boolean) => void;
  closeSettings: () => void;
  setExportOpen: (open: boolean) => void;
  setProblemsOpen: (open: boolean) => void;
  /** The Stand-in AI switch: on or off for every flow. */
  setStandIn: (on: boolean) => void;
  /** Use the stand-in AI for one flow only ("Try it" without a key), kept in this browser. */
  setFlowStandIn: (flowId: string, on: boolean) => void;
  focus: (step: string, key: string) => void;
  setTriggersOpen: (open: boolean) => void;
  /** Load the open flow's breakpoints and stand-in setting. */
  loadFlowPrefs: (flowId: string) => void;
  toggleBreakpoint: (step: string, where: "before" | "after") => void;
}

const initialTheme: Theme =
  typeof document !== "undefined" && document.documentElement.classList.contains("dark") ? "dark" : "light";

export const useUi = create<UiState>()((set, get) => ({
  mode: stored<Mode>("easychain.mode", "beginner"),
  theme: initialTheme,
  selected: [],
  rightTab: "inspect",
  settings: { open: false },
  importOpen: false,
  exportOpen: false,
  problemsOpen: false,
  standIn: storedStandIn(null),
  focusSetting: null,
  triggersOpen: false,
  breakpoints: { before: [], after: [] },
  prefsFor: null,
  setMode: (mode) => {
    persist("easychain.mode", mode);
    set({ mode });
  },
  toggleTheme: () => {
    const theme: Theme = get().theme === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("dark", theme === "dark");
    persist("easychain.theme", theme);
    set({ theme });
  },
  select: (selected) => {
    const prev = get().selected;
    if (prev.length === selected.length && prev.every((id, i) => id === selected[i])) return;
    set({ selected });
  },
  setRightTab: (rightTab) => set({ rightTab }),
  openSettings: (focus) => set({ settings: { open: true, ...focus } }),
  closeSettings: () => set({ settings: { open: false } }),
  setImportOpen: (importOpen) => set({ importOpen }),
  setExportOpen: (exportOpen) => set({ exportOpen }),
  setProblemsOpen: (problemsOpen) => set({ problemsOpen }),
  setStandIn: (standIn) => {
    persist(STAND_IN, String(standIn));
    // The switch works as it always has: a flow's own setting from "Try it" gives way to it.
    const flowId = get().prefsFor;
    if (flowId) forget(`${STAND_IN}.${flowId}`);
    set({ standIn });
  },
  setFlowStandIn: (flowId, on) => {
    persist(`${STAND_IN}.${flowId}`, String(on));
    if (get().prefsFor === flowId) set({ standIn: on });
  },
  focus: (step, key) => set({ selected: [step], rightTab: "inspect", focusSetting: { step, key, at: Date.now() } }),
  setTriggersOpen: (triggersOpen) => set({ triggersOpen }),
  loadFlowPrefs: (flowId) => set({ breakpoints: storedBreakpoints(flowId), standIn: storedStandIn(flowId), prefsFor: flowId }),
  toggleBreakpoint: (step, where) => {
    const current = get().breakpoints;
    const list = current[where].includes(step) ? current[where].filter((s) => s !== step) : [...current[where], step];
    const breakpoints = { ...current, [where]: list };
    const flowId = get().prefsFor;
    if (flowId) persist(`easychain.breakpoints.${flowId}`, JSON.stringify(breakpoints));
    set({ breakpoints });
  },
}));
