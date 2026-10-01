import { create } from "zustand";
import { api } from "../lib/api";
import type { Catalog, StepTypeInfo } from "../lib/types";

interface CatalogState {
  catalog: Catalog | null;
  error: string | null;
  load: () => Promise<void>;
  refreshProviders: () => Promise<void>;
}

export const useCatalog = create<CatalogState>()((set) => ({
  catalog: null,
  error: null,
  load: async () => {
    try {
      set({ catalog: await api.catalog(), error: null });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },
  refreshProviders: async () => {
    const catalog = await api.catalog();
    set({ catalog });
  },
}));

export function stepInfo(type: string): StepTypeInfo | undefined {
  return useCatalog.getState().catalog?.steps.find((s) => s.type === type);
}

export function useStepInfo(type: string | undefined): StepTypeInfo | undefined {
  return useCatalog((s) => s.catalog?.steps.find((info) => info.type === type));
}
