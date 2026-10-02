import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { KnowledgeHit } from "../../lib/types";
import { CitedText, isSources, passageText, SourceList } from "./Citations";

const hits = [
  { n: 1, id: "c1", doc_id: "d1", text: "## Refunds\n\nRefunds take 5 working days.", title: "Returns", source: "returns.md", heading: "Refunds", score: 0.03 },
  { n: 2, id: "c2", doc_id: "d2", text: "Cancel any time from Billing.", title: "Subscriptions", source: "subscriptions.md", score: 0.02 },
] as unknown as KnowledgeHit[];

describe("citations", () => {
  it("recognises Knowledge Base sources", () => {
    expect(isSources(hits)).toBe(true);
    expect(isSources([])).toBe(false);
    expect(isSources([{ n: 1, text: "x" }])).toBe(false);
    expect(isSources("text")).toBe(false);
  });

  it("drops Markdown heading lines from passages", () => {
    expect(passageText("## Refunds\n\nRefunds take 5 working days.")).toBe("Refunds take 5 working days.");
    expect(passageText("Price #1 is best\n# Title\nmore")).toBe("Price #1 is best\nmore");
  });

  it("links [n] to its source", () => {
    render(
      <p>
        <CitedText text="Refunds take 5 days [1]. Cancel from Billing [2]. See [9]." sources={hits} />
      </p>,
    );
    expect(screen.getByRole("link", { name: /source 1/i })).toHaveAttribute("href", "#source-1");
    expect(screen.getByRole("link", { name: /source 2/i })).toHaveAttribute("href", "#source-2");
    expect(screen.queryByRole("link", { name: /source 9/i })).toBeNull();
  });

  it("lists sources without heading markup", () => {
    render(<SourceList sources={hits} />);
    expect(screen.getByText("Refunds take 5 working days.")).toBeInTheDocument();
    expect(screen.queryByText(/## Refunds/)).toBeNull();
  });
});
