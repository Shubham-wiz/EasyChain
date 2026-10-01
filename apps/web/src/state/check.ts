import { create } from "zustand";
import type { Analysis, CompileResult, Issue } from "../lib/types";

interface CheckState {
  issues: Issue[];
  analysis: Analysis | null;
  compiled: CompileResult | null;
  checking: boolean;
  set: (patch: Partial<Omit<CheckState, "set">>) => void;
}

export const useCheck = create<CheckState>()((set) => ({
  issues: [],
  analysis: null,
  compiled: null,
  checking: false,
  set: (patch) => set(patch),
}));

export function issuesFor(issues: Issue[], step: string): Issue[] {
  return issues.filter((i) => i.step === step);
}
