import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import type { AuthStatus, FlowSpec, Me, Role } from "../lib/types";
import { can, useAuth } from "../state/auth";
import { useFlow } from "../state/flow";
import { startRun, useRun } from "../state/run";
import { useUi } from "../state/ui";
import { SettingsDialog } from "./dialogs/SettingsDialog";
import { TooltipProvider } from "./ui";

vi.mock("../lib/api", async (original) => ({
  ...(await original<typeof import("../lib/api")>()),
  api: { runStream: vi.fn().mockResolvedValue(undefined), secrets: vi.fn().mockResolvedValue([]), providers: vi.fn().mockResolvedValue([]), apiKeys: vi.fn().mockResolvedValue([]) },
}));

const roles: AuthStatus["roles"] = (["viewer", "runner", "editor", "owner"] as Role[]).map((id) => ({ id, label: id[0].toUpperCase() + id.slice(1) }));

function signedInAs(role: Role) {
  const me: Me = { id: "u1", email: `${role}@example.com`, name: "", role, role_label: role[0].toUpperCase() + role.slice(1), via: "session", workspace: "default" };
  useAuth.setState({ status: { mode: "on", signed_in: true, setup_needed: false, me, roles } });
}

const spec: FlowSpec = {
  version: 1,
  name: "Echo",
  description: "",
  data: [],
  steps: [
    { id: "input", type: "input", name: "Input", description: "", settings: { mode: "form", fields: [{ name: "text" }] } },
    { id: "output", type: "output", name: "Output", description: "", settings: { fields: ["text"] } },
  ],
  connections: [],
  canvas: { steps: {}, notes: [] },
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useAuth.setState({ status: null, error: null });
  useFlow.getState().setReadOnly(false);
  useUi.getState().closeSettings();
});

describe("roles in the web app", () => {
  it("acts as the Owner until the server says otherwise, and with sign-in off", () => {
    expect(can("owner")).toBe(true);
    useAuth.setState({ status: { mode: "off", signed_in: true, setup_needed: false, me: { id: null, email: null, name: "", role: "owner", role_label: "Owner", via: "local", workspace: "default" }, roles } });
    expect(can("owner")).toBe(true);
    signedInAs("runner");
    expect([can("viewer"), can("runner"), can("editor"), can("owner")]).toEqual([true, true, false, false]);
    useAuth.getState().signedOut();
    expect(can("viewer")).toBe(false);
  });

  it("ignores edits to a flow the role can't change", () => {
    useFlow.getState().load("f1", spec);
    useFlow.getState().setReadOnly(true);
    useFlow.getState().apply((s) => ({ ...s, name: "Changed" }));
    expect(useFlow.getState().spec).toBe(spec);
    expect(useFlow.getState().saveState).toBe("saved");
    useFlow.getState().setReadOnly(false);
    useFlow.getState().apply((s) => ({ ...s, name: "Changed" }));
    expect(useFlow.getState().spec?.name).toBe("Changed");
  });

  it("a Runner runs the saved flow; an Editor runs what's on the canvas", async () => {
    const runStream = api.runStream as ReturnType<typeof vi.fn>;
    useFlow.getState().load("f1", spec);
    signedInAs("runner");
    await startRun({ text: "hi" });
    expect(runStream.mock.calls[0][0]).toMatchObject({ flow_id: "f1", inputs: { text: "hi" } });
    expect(runStream.mock.calls[0][0].spec).toBeUndefined();
    useRun.getState().newChat();
    signedInAs("editor");
    await startRun({ text: "hi" });
    expect(runStream.mock.calls[1][0]).toMatchObject({ flow_id: "f1", spec });
  });

  it("shows each role the settings it may see", () => {
    const tabs = () => screen.getAllByRole("tab").map((t) => t.textContent);
    const open = (role: Role) => {
      cleanup();
      signedInAs(role);
      useUi.getState().openSettings();
      render(
        <TooltipProvider>
          <SettingsDialog />
        </TooltipProvider>,
      );
    };
    open("viewer");
    expect(tabs()).toEqual(["Keys for scripts"]);
    open("runner");
    expect(tabs()).toEqual(["Keys for scripts"]);
    open("editor");
    expect(tabs()).toEqual(["Keys and providers", "MCP servers", "Notifications", "Sandbox", "Keys for scripts"]);
    expect(screen.getByRole("note")).toHaveTextContent("Your role (Editor) can't change these; that needs Owner or above.");
    open("owner");
    expect(tabs()).toEqual(["Keys and providers", "MCP servers", "Notifications", "Sandbox", "People", "Keys for scripts", "Audit log"]);
    expect(screen.queryByRole("note")).toBeNull();
  });
});
