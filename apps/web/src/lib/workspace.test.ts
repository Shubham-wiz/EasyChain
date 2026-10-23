import { afterEach, describe, expect, it, vi } from "vitest";
import { api, chooseWorkspace, chosenWorkspace } from "./api";

afterEach(() => {
  chooseWorkspace(null);
  vi.unstubAllGlobals();
});

describe("the workspace a tab works in", () => {
  it("is sent with every request once chosen, and remembered", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response("[]", { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    await api.flows();
    expect(fetch.mock.calls[0][1].headers).not.toHaveProperty("X-EasyChain-Workspace");
    chooseWorkspace("team-b");
    expect(chosenWorkspace()).toBe("team-b");
    expect(sessionStorage.getItem("easychain.workspace")).toBe("team-b");
    expect(localStorage.getItem("easychain.workspace")).toBe("team-b");
    await api.flows();
    expect(fetch.mock.calls[1][1].headers).toMatchObject({ "X-EasyChain-Workspace": "team-b" });
    chooseWorkspace(null);
    expect(sessionStorage.getItem("easychain.workspace")).toBeNull();
  });
});
