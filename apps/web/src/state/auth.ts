import { create } from "zustand";
import { api, whenSignedOut } from "../lib/api";
import type { AuthStatus, Me, Role } from "../lib/types";

const RANK: Record<Role, number> = { viewer: 0, runner: 1, editor: 2, owner: 3 };

interface AuthState {
  status: AuthStatus | null;
  error: string | null;
  load: () => Promise<void>;
  /** The server said nobody is signed in (the session ran out, or a password changed elsewhere). */
  signedOut: () => void;
}

export const useAuth = create<AuthState>()((set, get) => ({
  status: null,
  error: null,
  load: async () => {
    try {
      set({ status: await api.authStatus(), error: null });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },
  signedOut: () => {
    const status = get().status;
    if (status?.mode === "on" && status.signed_in) set({ status: { ...status, signed_in: false, me: null } });
  },
}));

whenSignedOut(() => useAuth.getState().signedOut());

export function useMe(): Me | null {
  return useAuth((s) => s.status?.me ?? null);
}

/** Whether the signed-in person's role is at least `role` (always, with sign-in off). */
export function atLeast(me: Me | null, role: Role): boolean {
  return !!me && RANK[me.role] >= RANK[role];
}
