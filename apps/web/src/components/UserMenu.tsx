import * as Popover from "@radix-ui/react-popover";
import { KeyRound, LogOut, Users } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { useAuth, useMe } from "../state/auth";
import { useUi } from "../state/ui";
import { Badge, Button, Input } from "./ui";

function initials(name: string, email: string | null) {
  const words = (name || email || "?").split(/[\s@.]+/).filter(Boolean);
  return words
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
}

function ChangePassword({ done }: { done: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <form
      className="space-y-2 border-t border-border pt-3"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          await api.changePassword(current, next);
          done();
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }}
    >
      <Input type="password" aria-label="Current password" placeholder="Current password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
      <Input type="password" aria-label="New password" placeholder="New password (10+ characters)" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
      {error && <p className="text-xs text-danger">{error}</p>}
      <Button type="submit" size="sm" variant="primary" disabled={!current || !next}>
        Change password
      </Button>
      <p className="text-[11px] text-faint">Your other browsers are signed out.</p>
    </form>
  );
}

/** Who is signed in, and signing out (only when the server has sign-in on). */
export function UserMenu() {
  const mode = useAuth((s) => s.status?.mode);
  const me = useMe();
  const openSettings = useUi((s) => s.openSettings);
  const [open, setOpen] = useState(false);
  const [changing, setChanging] = useState(false);
  const [changed, setChanged] = useState(false);
  if (mode !== "on" || !me) return null;
  const go = (tab: "people" | "api-keys") => {
    setOpen(false);
    openSettings({ tab });
  };
  return (
    <Popover.Root
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setChanging(false);
      }}
    >
      <Popover.Trigger asChild>
        <button
          type="button"
          aria-label="Your account"
          className="flex h-7 w-7 items-center justify-center rounded-full bg-accent-soft text-[11px] font-semibold text-accent hover:ring-2 hover:ring-accent/30"
          data-testid="user-menu"
        >
          {initials(me.name, me.email)}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" sideOffset={6} className="z-50 w-72 space-y-3 rounded-xl border border-border bg-surface p-3 shadow-xl">
          <div className="space-y-1">
            <p className="truncate text-sm font-semibold">{me.name || me.email}</p>
            {me.name && <p className="truncate text-xs text-muted">{me.email}</p>}
            <Badge tone="accent">{me.role_label}</Badge>
          </div>
          <div className="flex flex-col items-start gap-0.5">
            <Button variant="ghost" size="sm" onClick={() => go("api-keys")}>
              <KeyRound size={13} /> Keys for scripts
            </Button>
            {me.role === "owner" && (
              <Button variant="ghost" size="sm" onClick={() => go("people")}>
                <Users size={13} /> People
              </Button>
            )}
            {!changing && (
              <Button variant="ghost" size="sm" onClick={() => setChanging(true)}>
                {changed ? "Password changed" : "Change password"}
              </Button>
            )}
          </div>
          {changing && (
            <ChangePassword
              done={() => {
                setChanging(false);
                setChanged(true);
              }}
            />
          )}
          <Button
            variant="outline"
            size="sm"
            className="w-full justify-center"
            onClick={async () => {
              await api.signOut().catch(() => undefined);
              // Start afresh, so the next person doesn't see what was open.
              window.history.replaceState(null, "", "/");
              window.location.reload();
            }}
          >
            <LogOut size={13} /> Sign out
          </Button>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
