import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { GatewayState, Spending } from "../../lib/types";
import { GatewaySection, money } from "./Gateway";

vi.mock("../../lib/api", async (original) => ({
  ...(await original<typeof import("../../lib/api")>()),
  api: { gateway: vi.fn(), saveGateway: vi.fn(), forgetAnswers: vi.fn(), spending: vi.fn() },
}));

vi.mock("../../state/auth", async (original) => ({
  ...(await original<typeof import("../../state/auth")>()),
  useCan: () => true,
}));

const mocked = api as unknown as Record<"gateway" | "saveGateway" | "forgetAnswers" | "spending", ReturnType<typeof vi.fn>>;

const status = (scope: string, label: string, extra = {}) => ({ scope, label, budget: null, period: "2026-10", since: 0, until: 0, spent: 0, share: null, ...extra });

const state: GatewayState = {
  settings: { budgets: { production: { limit: 50, period: "month", alert_at: 0.8, stop: true } }, cache: { scopes: [], hours: 24 }, fallbacks: {}, retries: null, per_second: {}, prices: {} },
  budgets: [
    status("workspace", "Workspace"),
    status("drafts", "Drafts", { spent: 1.25 }),
    status("dev", "Development"),
    status("staging", "Staging"),
    status("production", "Production", { budget: { limit: 50, period: "month", alert_at: 0.8, stop: true }, spent: 42, share: 0.84 }),
  ] as GatewayState["budgets"],
  alerts: [{ budget: "production", period: "2026-10", level: 80, spent: 40.5, amount: 50, at: 1 }],
};

const spending: Spending = {
  period: "month",
  by: "flow",
  since: 0,
  total: { calls: 3, cached: 1, errors: 0, unpriced: 1, input_tokens: 1200, output_tokens: 300, cost: 43.25 },
  rows: [
    { key: "support", label: "Support bot", calls: 2, cached: 1, errors: 0, unpriced: 0, input_tokens: 1000, output_tokens: 200, cost: 43, first: 1, last: 2 },
    { key: null, label: "Copilot", calls: 1, cached: 0, errors: 0, unpriced: 1, input_tokens: 200, output_tokens: 100, cost: 0.25, first: 1, last: 1 },
  ],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Settings → Model gateway", () => {
  it("shows spending, budgets and alerts, and saves budgets, caching, fallbacks and prices", async () => {
    mocked.gateway.mockResolvedValue(state);
    mocked.spending.mockResolvedValue(spending);
    mocked.saveGateway.mockResolvedValue(state);
    render(<GatewaySection />);
    expect(await screen.findByText("Support bot")).toBeTruthy();
    expect(screen.getByTestId("spending-total").textContent).toBe("$43.25 · 3 model calls, 1 from the cache");
    expect(screen.getByText(/1 call has no price/)).toBeTruthy();
    expect(await screen.findByTestId("budget-production-spent")).toHaveProperty("textContent", "$42.00 of $50.00 this month");
    expect(screen.getByRole("meter", { name: "Production budget used" }).getAttribute("aria-valuenow")).toBe("84");
    expect(screen.getByTestId("budget-alerts").textContent).toContain("Production: 80% used ($40.50 of $50.00, 2026-10)");

    fireEvent.change(screen.getByLabelText("Drafts budget in dollars"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Drafts budget period"), { target: { value: "day" } });
    fireEvent.change(screen.getByLabelText("Drafts alert at percent"), { target: { value: "50" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Development" }));
    fireEvent.click(screen.getByRole("button", { name: "Add a fallback" }));
    fireEvent.change(screen.getByLabelText("Fallbacks: openai:gpt-4.1 1"), { target: { value: "openai:gpt-4.1" } });
    fireEvent.change(screen.getByLabelText("Fallbacks: anthropic:claude-sonnet-4-5, … 1"), { target: { value: "anthropic:claude-sonnet-4-5, openai:gpt-4o" } });
    fireEvent.click(screen.getByRole("button", { name: "Add a price" }));
    fireEvent.change(screen.getByLabelText("Prices: openai:ft:gpt-4.1:acme 1"), { target: { value: "openai:ft:acme" } });
    fireEvent.change(screen.getByLabelText("Prices: 3 / 12 1"), { target: { value: "3 / 12" } });
    fireEvent.change(screen.getByLabelText("Retries"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the model gateway" }));
    await waitFor(() => expect(mocked.saveGateway).toHaveBeenCalled());
    expect(mocked.saveGateway.mock.calls[0][0]).toEqual({
      budgets: {
        drafts: { limit: 5, period: "day", alert_at: 0.5, stop: true },
        production: { limit: 50, period: "month", alert_at: 0.8, stop: true },
      },
      cache: { scopes: ["dev"], hours: 24 },
      fallbacks: { "openai:gpt-4.1": ["anthropic:claude-sonnet-4-5", "openai:gpt-4o"] },
      retries: 2,
      per_second: {},
      prices: { "openai:ft:acme": { input: 3, output: 12 } },
    });
  });

  it("shows what the server refused", async () => {
    mocked.gateway.mockResolvedValue(state);
    mocked.spending.mockResolvedValue({ ...spending, rows: [], total: { ...spending.total, calls: 0, unpriced: 0 } });
    mocked.saveGateway.mockRejectedValue(Object.assign(new Error("These settings can't be saved yet."), { problems: ["There's no provider “nobody”."] }));
    render(<GatewaySection />);
    expect(await screen.findByText("No model calls in this period.")).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Save the model gateway" }));
    expect((await screen.findByRole("alert")).textContent).toBe("There's no provider “nobody”.");
  });

  it("writes money plainly", () => {
    expect(money(0)).toBe("$0.00");
    expect(money(12.5)).toBe("$12.50");
    expect(money(0.0042)).toBe("$0.0042");
  });
});
