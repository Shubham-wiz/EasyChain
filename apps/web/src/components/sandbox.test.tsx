import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import { applyFix } from "../lib/fixes";
import type { SandboxSettings, SandboxStatus } from "../lib/types";
import { useUi } from "../state/ui";
import { SandboxSection } from "./dialogs/Sandbox";
import { AddressInput, TooltipProvider } from "./ui";

const renderSection = () =>
  render(
    <TooltipProvider>
      <SandboxSection />
    </TooltipProvider>,
  );

function Address({ initial, onChange }: { initial: string; onChange: (v: string) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <AddressInput
      aria-label="Webhook URL"
      value={value}
      onChange={(v) => {
        onChange(v);
        setValue(v);
      }}
    />
  );
}

vi.mock("../lib/api", async (original) => ({
  ...(await original<typeof import("../lib/api")>()),
  api: { sandboxSettings: vi.fn(), saveSandboxSettings: vi.fn(), sandboxStatus: vi.fn(), prepareSandbox: vi.fn() },
}));

const mocked = api as unknown as Record<"sandboxSettings" | "saveSandboxSettings" | "sandboxStatus" | "prepareSandbox", ReturnType<typeof vi.fn>>;

const settings: SandboxSettings = { mode: "auto", image: "python:3.12-slim", runtime: "auto", memory_mb: 256, cpus: 1, pids: 64, timeout: 120 };
const noDocker: SandboxStatus = { available: false, reason: "Docker isn't installed.", version: null, gvisor: false, mode: "auto", effective: "off" };
const docker: SandboxStatus = { available: true, reason: null, version: "29.6", gvisor: true, mode: "auto", effective: "docker" };

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Settings → Sandbox", () => {
  it("says plainly when Code steps run without a sandbox, and saves a new mode", async () => {
    mocked.sandboxSettings.mockResolvedValue({ ...settings });
    mocked.sandboxStatus.mockResolvedValue(noDocker);
    mocked.saveSandboxSettings.mockImplementation(async (body: SandboxSettings) => body);
    renderSection();
    expect(await screen.findByText(/run in Easy Chain's own process, because Docker isn't available/)).toBeInTheDocument();
    expect(screen.getByText("Docker isn't installed.")).toBeInTheDocument();
    expect(screen.queryByTestId("sandbox-prepare")).toBeNull(); // nothing to prepare without Docker
    fireEvent.change(screen.getByLabelText("Run Code steps in the sandbox"), { target: { value: "docker" } });
    fireEvent.change(screen.getByLabelText("Memory (MB)"), { target: { value: "512" } });
    fireEvent.click(screen.getByRole("button", { name: "Save sandbox settings" }));
    await waitFor(() => expect(mocked.saveSandboxSettings).toHaveBeenCalledWith({ ...settings, mode: "docker", memory_mb: 512 }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("shows Docker and gVisor when they are there, and prepares the image", async () => {
    mocked.sandboxSettings.mockResolvedValue({ ...settings });
    mocked.sandboxStatus.mockResolvedValue(docker);
    mocked.prepareSandbox.mockResolvedValue({ ok: true, message: "The sandbox image python:3.12-slim is ready." });
    renderSection();
    expect(await screen.findByText(/Code steps run in a sandbox \(Docker 29.6, with gVisor\)/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("sandbox-prepare"));
    expect(await screen.findByText("The sandbox image python:3.12-slim is ready.")).toBeInTheDocument();
  });

  it("is where the check's fix takes you", () => {
    applyFix({ kind: "open_settings", label: "Sandbox settings", params: { tab: "sandbox" } });
    expect(useUi.getState().settings).toMatchObject({ open: true, tab: "sandbox" });
  });
});

describe("AddressInput", () => {
  it("shows a saved address with its tokens hidden, and keeps it unless a new one is typed", () => {
    const onChange = vi.fn();
    const hidden = "https://hooks.example.com/run/••••••?token=••••••";
    render(<Address initial={hidden} onChange={onChange} />);
    const input = screen.getByLabelText("Webhook URL");
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("placeholder", hidden);
    expect(input).toHaveAccessibleDescription(/type a new address to replace it/);
    fireEvent.change(input, { target: { value: "https://new.example.com/hook" } });
    expect(onChange).toHaveBeenLastCalledWith("https://new.example.com/hook");
    expect(input).toHaveValue("https://new.example.com/hook");
    fireEvent.change(input, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(hidden); // emptied again: the saved address stays
    expect(input).toHaveValue("");
  });
});
