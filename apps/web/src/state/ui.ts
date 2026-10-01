import { create } from "zustand";

export type Mode = "beginner" | "pro";
export type Theme = "light" | "dark";
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

interface UiState {
  mode: Mode;
  theme: Theme;
  selected: string[];
  rightTab: RightTab;
  settings: { open: boolean; provider?: string; secret?: string };
  exportOpen: boolean;
  problemsOpen: boolean;
  standIn: boolean;
  focusSetting: { step: string; key: string; at: number } | null;
  setMode: (mode: Mode) => void;
  toggleTheme: () => void;
  select: (ids: string[]) => void;
  setRightTab: (tab: RightTab) => void;
  openSettings: (focus?: { provider?: string; secret?: string }) => void;
  closeSettings: () => void;
  setExportOpen: (open: boolean) => void;
  setProblemsOpen: (open: boolean) => void;
  setStandIn: (on: boolean) => void;
  focus: (step: string, key: string) => void;
}

const initialTheme: Theme =
  typeof document !== "undefined" && document.documentElement.classList.contains("dark") ? "dark" : "light";

export const useUi = create<UiState>()((set, get) => ({
  mode: stored<Mode>("easychain.mode", "beginner"),
  theme: initialTheme,
  selected: [],
  rightTab: "inspect",
  settings: { open: false },
  exportOpen: false,
  problemsOpen: false,
  standIn: stored<string>("easychain.standIn", "false") === "true",
  focusSetting: null,
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
  setExportOpen: (exportOpen) => set({ exportOpen }),
  setProblemsOpen: (problemsOpen) => set({ problemsOpen }),
  setStandIn: (standIn) => {
    persist("easychain.standIn", String(standIn));
    set({ standIn });
  },
  focus: (step, key) => set({ selected: [step], rightTab: "inspect", focusSetting: { step, key, at: Date.now() } }),
}));
