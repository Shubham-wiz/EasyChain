import * as Popover from "@radix-ui/react-popover";
import { Check, ChevronDown, Layers } from "lucide-react";
import { useEffect, useState } from "react";
import { api, chooseWorkspace } from "../lib/api";
import type { WorkspaceInfo } from "../lib/types";
import { useServerOwner } from "../state/auth";
import { useUi } from "../state/ui";
import { Badge, Button } from "./ui";

/** Work in another workspace: the app starts afresh in it (docs/workspaces.md). */
export function switchTo(id: string) {
  chooseWorkspace(id);
  window.history.replaceState(null, "", "/");
  window.location.reload();
}

/** Which workspace this tab works in, and the others you're in. Hidden while there's only one,
 * except for the server's Owners, who make workspaces. */
export function WorkspaceSwitcher() {
  const serverOwner = useServerOwner();
  const openSettings = useUi((s) => s.openSettings);
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[] | null>(null);
  const [current, setCurrent] = useState<string>("default");
  const [open, setOpen] = useState(false);
  useEffect(() => {
    api
      .workspaces()
      .then((r) => {
        setWorkspaces(r.workspaces);
        setCurrent(r.current);
      })
      .catch(() => setWorkspaces([]));
  }, []);
  const mine = (workspaces ?? []).filter((w) => w.role !== null);
  if (!workspaces || (mine.length < 2 && !serverOwner)) return null;
  const here = workspaces.find((w) => w.id === current);
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <Button variant="ghost" size="sm" aria-label={`Workspace: ${here?.name ?? current}`} data-testid="workspace-switcher">
          <Layers size={14} /> <span className="max-w-40 truncate">{here?.name ?? current}</span> <ChevronDown size={12} />
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="start" sideOffset={6} className="z-50 w-72 space-y-2 rounded-xl border border-border bg-surface p-2 shadow-xl">
          <p className="px-2 pt-1 text-xs text-muted">Each workspace has its own flows, runs, Inbox, Knowledge Bases and secrets.</p>
          <ul aria-label="Your workspaces" className="space-y-0.5">
            {mine.map((w) => (
              <li key={w.id}>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-surface-2"
                  aria-current={w.id === current ? "true" : undefined}
                  onClick={() => (w.id === current ? setOpen(false) : switchTo(w.id))}
                >
                  <span className="w-4">{w.id === current && <Check size={14} />}</span>
                  <span className="min-w-0 flex-1 truncate">{w.name}</span>
                  {w.role_label && <Badge>{w.role_label}</Badge>}
                </button>
              </li>
            ))}
          </ul>
          {serverOwner && (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              onClick={() => {
                setOpen(false);
                openSettings({ tab: "workspaces" });
              }}
            >
              Manage workspaces
            </Button>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
