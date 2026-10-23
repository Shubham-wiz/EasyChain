import { Layers, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { WorkspaceInfo } from "../../lib/types";
import { Badge, Button, Input } from "../ui";
import { switchTo } from "../WorkspaceSwitcher";

function message(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

function WorkspaceRow({ workspace, current, reload, onError }: { workspace: WorkspaceInfo; current: string; reload: () => void; onError: (m: string | null) => void }) {
  const [name, setName] = useState(workspace.name);
  return (
    <li className="space-y-2 rounded-lg border border-border p-2.5" data-testid={`workspace-${workspace.id}`}>
      <form
        className="flex items-center gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await api.renameWorkspace(workspace.id, name.trim());
            onError(null);
            reload();
          } catch (err) {
            onError(message(err));
          }
        }}
      >
        <Input aria-label={`Name of ${workspace.id}`} className="h-8 flex-1" value={name} onChange={(e) => setName(e.target.value)} />
        {name.trim() !== workspace.name && (
          <Button type="submit" size="sm" variant="outline" disabled={!name.trim()}>
            Rename
          </Button>
        )}
      </form>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span className="font-mono">{workspace.id}</span>
        <span>
          {workspace.people} {workspace.people === 1 ? "person" : "people"}
        </span>
        {workspace.role_label ? <Badge tone="accent">You: {workspace.role_label}</Badge> : <span>You aren't in it</span>}
        {workspace.id === current ? (
          <Badge tone="ok">This one</Badge>
        ) : (
          workspace.role && (
            <Button size="sm" variant="ghost" onClick={() => switchTo(workspace.id)}>
              Switch to it
            </Button>
          )
        )}
      </div>
    </li>
  );
}

/** The server's Owners: every workspace, and making new ones. */
export function WorkspacesSection() {
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([]);
  const [current, setCurrent] = useState("default");
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [made, setMade] = useState<WorkspaceInfo | null>(null);
  const load = () => {
    api
      .workspaces()
      .then((r) => {
        setWorkspaces(r.workspaces);
        setCurrent(r.current);
      })
      .catch((err) => setError(message(err)));
  };
  useEffect(load, []);
  return (
    <section className="space-y-3" aria-label="Workspaces">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold">
        <Layers size={14} /> Workspaces
      </h3>
      <p className="text-xs text-muted">
        Each workspace has its own flows, runs, Inbox, triggers, Knowledge Bases, skills, secrets and people. The sandbox, the network and single sign-on apply to all of them, and
        only the server's Owners (Owners of the default workspace) change those.
      </p>
      <ul className="space-y-2">
        {workspaces.map((w) => (
          <WorkspaceRow key={`${w.id}:${w.name}`} workspace={w} current={current} reload={load} onError={setError} />
        ))}
      </ul>
      {error && (
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      )}
      <form
        className="space-y-2 rounded-lg border border-dashed border-border p-3"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            setMade(await api.createWorkspace(name.trim()));
            setName("");
            setError(null);
            load();
          } catch (err) {
            setError(message(err));
          }
        }}
      >
        <p className="text-sm font-medium">New workspace</p>
        <div className="flex gap-2">
          <Input aria-label="New workspace name" placeholder="Name, like Sales team" value={name} onChange={(e) => setName(e.target.value)} />
          <Button type="submit" variant="outline" size="sm" disabled={!name.trim()}>
            <Plus size={13} /> Make it
          </Button>
        </div>
        <p className="text-[11px] text-faint">You'll be its first Owner. Add people to it from Settings → People once you've switched to it.</p>
        {made && (
          <p className="flex items-center gap-2 text-xs" role="status">
            Made {made.name}.
            <Button size="sm" variant="ghost" onClick={() => switchTo(made.id)}>
              Switch to it
            </Button>
          </p>
        )}
      </form>
    </section>
  );
}
