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
  standIn: boolean;
  focusSetting: { step: string; key: string; at: number } | null;
  triggersOpen: boolean;
  /** Breakpoints for test runs of the open flow (kept per flow in this browser). */
  breakpoints: Breakpoints;
  breakpointsFor: string | null;
  setMode: (mode: Mode) => void;
  toggleTheme: () => void;
  select: (ids: string[]) => void;
  setRightTab: (tab: RightTab) => void;
  openSettings: (focus?: { provider?: string; secret?: string; tab?: SettingsTab }) => void;
  setImportOpen: (open: boolean) => void;
  closeSettings: () => void;
  setExportOpen: (open: boolean) => void;
  setProblemsOpen: (open: boolean) => void;
  setStandIn: (on: boolean) => void;
  focus: (step: string, key: string) => void;
  setTriggersOpen: (open: boolean) => void;
  loadBreakpoints: (flowId: string) => void;
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
  standIn: stored<string>("easychain.standIn", "false") === "true",
  focusSetting: null,
  triggersOpen: false,
  breakpoints: { before: [], after: [] },
  breakpointsFor: null,
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
    persist("easychain.standIn", String(standIn));
    set({ standIn });
  },
  focus: (step, key) => set({ selected: [step], rightTab: "inspect", focusSetting: { step, key, at: Date.now() } }),
  setTriggersOpen: (triggersOpen) => set({ triggersOpen }),
  loadBreakpoints: (flowId) => set({ breakpoints: storedBreakpoints(flowId), breakpointsFor: flowId }),
  toggleBreakpoint: (step, where) => {
    const current = get().breakpoints;
    const list = current[where].includes(step) ? current[where].filter((s) => s !== step) : [...current[where], step];
    const breakpoints = { ...current, [where]: list };
    const flowId = get().breakpointsFor;
    if (flowId) persist(`easychain.breakpoints.${flowId}`, JSON.stringify(breakpoints));
    set({ breakpoints });
  },
}));
