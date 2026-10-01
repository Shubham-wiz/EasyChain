import { Box, Code2, Globe, LogIn, LogOut, ScrollText, Sparkles, Split, type LucideIcon } from "lucide-react";
import type { Step } from "../../lib/types";

export const ICONS: Record<string, LucideIcon> = {
  "log-in": LogIn,
  "log-out": LogOut,
  sparkles: Sparkles,
  "scroll-text": ScrollText,
  globe: Globe,
  code: Code2,
  split: Split,
};

export function iconFor(name: string | undefined): LucideIcon {
  return (name && ICONS[name]) || Box;
}

export const CATEGORY_COLORS: Record<string, { chip: string; ring: string }> = {
  start_end: { chip: "bg-emerald-500/12 text-emerald-600 dark:text-emerald-400", ring: "#10b981" },
  ai: { chip: "bg-violet-500/12 text-violet-600 dark:text-violet-400", ring: "#8b5cf6" },
  actions: { chip: "bg-sky-500/12 text-sky-600 dark:text-sky-400", ring: "#0ea5e9" },
  logic: { chip: "bg-amber-500/14 text-amber-600 dark:text-amber-400", ring: "#f59e0b" },
};

export function colorsFor(category: string | undefined) {
  return CATEGORY_COLORS[category ?? ""] ?? CATEGORY_COLORS.actions;
}

/** One-line description of what a step is set up to do, shown on its card. */
export function stepSummary(step: Step, upstream?: string | null): string {
  const s = step.settings;
  switch (step.type) {
    case "input":
      if (s.mode === "chat") return "Chat message" + (s.fields?.length ? ` + ${s.fields.length} field(s)` : "");
      return s.fields?.length ? s.fields.map((f: { name: string }) => f.name).join(", ") : "No fields yet";
    case "output":
      return s.fields?.length ? `Returns ${s.fields.join(", ")}` : "Returns everything";
    case "ai_model": {
      const model = String(s.model ?? "").split(":").slice(1).join(":") || s.model;
      return `${model} · ${s.prompt || upstream || "?"} → ${s.save_as}`;
    }
    case "instructions": {
      const text = String(s.user || s.system || "").replace(/\s+/g, " ").trim();
      return text ? text : "Empty";
    }
    case "http_request":
      return `${s.method} ${s.url || "(no URL)"} → ${s.save_as}`;
    case "code": {
      const match = /"""([^"]+)"""/.exec(String(s.code ?? ""));
      return match ? match[1].trim() : "Python code";
    }
    case "decision":
      return s.mode === "ai" ? `AI decides from ${s.input || upstream || "?"}` : `${(s.exits ?? []).length} rule(s)`;
    default:
      return "";
  }
}
