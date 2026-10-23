import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import type { AuthStatus, Me } from "../lib/types";
import { useAuth } from "../state/auth";
import { ApiKeysSection } from "./dialogs/People";
import { SignIn } from "./SignIn";
import { TooltipProvider } from "./ui";

vi.mock("../lib/api", async (original) => {
  const real = await original<typeof import("../lib/api")>();
  return { ...real, api: { authStatus: vi.fn(), setup: vi.fn(), signIn: vi.fn(), apiKeys: vi.fn(), createApiKey: vi.fn(), revokeApiKey: vi.fn() } };
});

const mocked = api as unknown as Record<"authStatus" | "setup" | "signIn" | "apiKeys" | "createApiKey" | "revokeApiKey", ReturnType<typeof vi.fn>>;

const roles: AuthStatus["roles"] = [
  { id: "viewer", label: "Viewer" },
  { id: "runner", label: "Runner" },
  { id: "editor", label: "Editor" },
  { id: "owner", label: "Owner" },
];
const editor: Me = { id: "u1", email: "ed@example.com", name: "Ed", role: "editor", role_label: "Editor", via: "session", workspace: "default" };
const signedOut: AuthStatus = { mode: "on", signed_in: false, setup_needed: false, me: null, roles };

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useAuth.setState({ status: null, error: null });
});

describe("signing in", () => {
  it("makes the first Owner with the code from the server's log", async () => {
    mocked.setup.mockResolvedValue({ me: { ...editor, role: "owner" } });
    mocked.authStatus.mockResolvedValue({ ...signedOut, signed_in: true, me: { ...editor, role: "owner" } });
    render(<SignIn setup />);
    expect(screen.getByText(/docker compose logs easychain/)).toBeInTheDocument();
    const make = screen.getByRole("button", { name: "Make the account" });
    expect(make).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Code from the server's log"), { target: { value: " abcd-efgh-jklm " } });
    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "Olive" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "olive@example.com" } });
    fireEvent.change(screen.getByLabelText("Password (at least 10 characters)"), { target: { value: "correct horse battery" } });
    fireEvent.click(make);
    await waitFor(() => expect(useAuth.getState().status?.signed_in).toBe(true));
    expect(mocked.setup).toHaveBeenCalledWith({ code: "abcd-efgh-jklm", email: "olive@example.com", name: "Olive", password: "correct horse battery" });
  });

  it("says plainly when the password is wrong, and how to get a new one", async () => {
    mocked.signIn.mockRejectedValue(new Error("That email and password don't match an account."));
    render(<SignIn setup={false} />);
    expect(screen.getByText(/easychain users password/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ed@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("don't match an account");
    expect(mocked.authStatus).not.toHaveBeenCalled();
  });

  it("asks to sign in again, over the page, when the session runs out", async () => {
    useAuth.setState({ status: { ...signedOut, signed_in: true, me: editor } });
    useAuth.getState().signedOut();
    expect(useAuth.getState().status).toMatchObject({ signed_in: false, me: null });
    render(<SignIn setup={false} overlay />);
    expect(screen.getByRole("main", { name: "Sign in again" })).toBeInTheDocument();
    expect(screen.getByText(/carry on where you were/)).toBeInTheDocument();
  });
});

describe("Settings → Keys for scripts", () => {
  it("shows a new key once, offers only roles up to yours, and revokes", async () => {
    useAuth.setState({ status: { ...signedOut, signed_in: true, me: editor } });
    mocked.apiKeys.mockResolvedValueOnce([]).mockResolvedValue([{ id: "k1", user_id: "u1", name: "Support site", role: "runner", created_at: Date.now() / 1000, last_used_at: null, mine: true }]);
    mocked.createApiKey.mockResolvedValue({ id: "k1", name: "Support site", role: "runner", key: "eck_k1_secret", created_at: Date.now() / 1000 });
    mocked.revokeApiKey.mockResolvedValue({ revoked: "k1" });
    render(
      <TooltipProvider>
        <ApiKeysSection />
      </TooltipProvider>,
    );
    expect(await screen.findByText("No keys yet.")).toBeInTheDocument();
    const role = screen.getByLabelText("The key's role");
    expect([...role.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["Editor (yours)", "Viewer", "Runner"]);
    fireEvent.change(screen.getByLabelText("What the key is for"), { target: { value: "Support site" } });
    fireEvent.change(role, { target: { value: "runner" } });
    fireEvent.click(screen.getByRole("button", { name: "Make a key" }));
    expect(await screen.findByLabelText("The new key")).toHaveValue("eck_k1_secret");
    expect(screen.getByText(/isn't shown again/)).toBeInTheDocument();
    expect(mocked.createApiKey).toHaveBeenCalledWith({ name: "Support site", role: "runner" });
    fireEvent.click(await screen.findByRole("button", { name: "Revoke Support site" }));
    await waitFor(() => expect(screen.queryByLabelText("The new key")).toBeNull());
    expect(mocked.revokeApiKey).toHaveBeenCalledWith("k1");
  });
});
