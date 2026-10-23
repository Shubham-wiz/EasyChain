import { create } from "zustand";
import { api, chooseWorkspace, whenSignedOut, whenWorkspaceGone } from "../lib/api";
import type { AuthStatus, Me, Role } from "../lib/types";

const RANK: Record<Role, number> = { viewer: 0, runner: 1, editor: 2, owner: 3 };
const LABEL: Record<Role, string> = { viewer: "Viewer", runner: "Runner", editor: "Editor", owner: "Owner" };

/** Before the server has said (and with sign-in off), the app acts as the Owner; the server decides. */
const LOCAL_OWNER: Me = { id: null, email: null, name: "", role: "owner", role_label: "Owner", via: "local", workspace: "default", server_owner: true };

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

// This tab's workspace isn't yours any more (you were taken out of it): start again in yours.
whenWorkspaceGone(() => {
  chooseWorkspace(null);
  window.location.assign("/");
});

export function useMe(): Me | null {
  return useAuth((s) => s.status?.me ?? null);
}

function current(status: AuthStatus | null): Me | null {
  return status ? status.me : LOCAL_OWNER;
}

/** Whether the person signed in may do what needs `role` (server/auth.py has the same table). */
export function useCan(role: Role): boolean {
  return useAuth((s) => atLeast(current(s.status), role));
}

export function can(role: Role): boolean {
  return atLeast(current(useAuth.getState().status), role);
}

/** An Owner of the default workspace: changes what every workspace shares (always, with sign-in off). */
export function useServerOwner(): boolean {
  return useAuth((s) => {
    const me = current(s.status);
    return !!me && me.role === "owner" && me.server_owner !== false;
  });
}

/** "Your role (Viewer) can't …; it needs Editor or above. An Owner can change your role." */
export function useRoleNote(role: Role, what: string): string {
  const me = useAuth((s) => current(s.status));
  return `Your role (${me?.role_label ?? "none"}) can't ${what}; that needs ${LABEL[role]} or above. An Owner can change your role.`;
}

/** Whether the signed-in person's role is at least `role` (always, with sign-in off). */
export function atLeast(me: Me | null, role: Role): boolean {
  return !!me && RANK[me.role] >= RANK[role];
}
