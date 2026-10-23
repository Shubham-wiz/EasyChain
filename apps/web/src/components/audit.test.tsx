import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import type { AuditEntry, AuditPage } from "../lib/types";
import { useAuth } from "../state/auth";
import { AuditSection } from "./dialogs/Audit";

vi.mock("../lib/api", async (original) => ({
  ...(await original<typeof import("../lib/api")>()),
  api: { audit: vi.fn(), people: vi.fn() },
}));

const mocked = api as unknown as Record<"audit" | "people", ReturnType<typeof vi.fn>>;

const entry = (id: number, text: string, extra: Partial<AuditEntry> = {}): AuditEntry => ({
  id,
  at: Date.now() / 1000,
  actor: "Olive",
  actor_id: "u1",
  via: "session",
  action: "flow.save",
  target: "support-bot",
  detail: {},
  address: "127.0.0.1",
  text,
  ...extra,
});
const kinds = [
  { id: "flow.", label: "Flows" },
  { id: "person.", label: "People" },
];

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useAuth.setState({ status: null, error: null });
});

describe("Settings → Audit log", () => {
  it("lists who changed what, filters by kind and person, and pages back", async () => {
    useAuth.setState({
      status: { mode: "on", signed_in: true, setup_needed: false, me: null, roles: [] },
    });
    mocked.people.mockResolvedValue([{ id: "u2", email: "ed@example.com", name: "Ed", role: "editor", role_label: "Editor", disabled: false, created_at: 0, last_login_at: null }]);
    const first: AuditPage = { entries: [entry(3, "Olive saved flow support-bot"), entry(2, "Ed started a run of support-bot", { via: "key" })], next: 2, kinds };
    const older: AuditPage = { entries: [entry(1, "the command line added ed@example.com", { via: "cli" })], next: null, kinds };
    mocked.audit.mockImplementation(async (f: { before?: number; kind?: string }) => (f.before ? older : f.kind ? { ...first, entries: [first.entries[0]], next: null } : first));
    render(<AuditSection />);
    const list = await screen.findByTestId("audit-entries");
    await waitFor(() => expect(list.querySelectorAll("li")).toHaveLength(2));
    expect(list).toHaveTextContent("Ed started a run of support-bot");
    expect(list).toHaveTextContent("key");
    fireEvent.click(screen.getByRole("button", { name: "Show older" }));
    await waitFor(() => expect(list.querySelectorAll("li")).toHaveLength(3));
    expect(list).toHaveTextContent("command line");
    expect(screen.queryByRole("button", { name: "Show older" })).toBeNull();

    fireEvent.change(screen.getByLabelText("What changed"), { target: { value: "flow." } });
    await waitFor(() => expect(mocked.audit).toHaveBeenLastCalledWith({ kind: "flow.", person: "", limit: 50 }));
    await waitFor(() => expect(screen.getByTestId("audit-entries").querySelectorAll("li")).toHaveLength(1));
    fireEvent.change(await screen.findByLabelText("Who"), { target: { value: "u2" } });
    await waitFor(() => expect(mocked.audit).toHaveBeenLastCalledWith({ kind: "flow.", person: "u2", limit: 50 }));
  });
});
