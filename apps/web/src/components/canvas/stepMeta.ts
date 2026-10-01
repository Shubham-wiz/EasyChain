import { Box, Code2, CornerDownRight, Globe, Layers, LogIn, LogOut, Repeat, ScrollText, Sparkles, Split, UserCheck, type LucideIcon } from "lucide-react";
import type { Step } from "../../lib/types";

export const ICONS: Record<string, LucideIcon> = {
  "log-in": LogIn,
  "log-out": LogOut,
  sparkles: Sparkles,
  "scroll-text": ScrollText,
  globe: Globe,
  code: Code2,
  split: Split,
  repeat: Repeat,
  layers: Layers,
  "user-check": UserCheck,
  "corner-down-right": CornerDownRight,
};

export function iconFor(name: string | undefined): LucideIcon {
  return (name && ICONS[name]) || Box;
}

export const CATEGORY_COLORS: Record<string, { chip: string; ring: string }> = {
  start_end: { chip: "bg-emerald-500/12 text-emerald-600 dark:text-emerald-400", ring: "#10b981" },
  ai: { chip: "bg-violet-500/12 text-violet-600 dark:text-violet-400", ring: "#8b5cf6" },
  actions: { chip: "bg-sky-500/12 text-sky-600 dark:text-sky-400", ring: "#0ea5e9" },
  logic: { chip: "bg-amber-500/14 text-amber-600 dark:text-amber-400", ring: "#f59e0b" },
  people: { chip: "bg-rose-500/12 text-rose-600 dark:text-rose-400", ring: "#f43f5e" },
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
    case "decision": {
      const base = s.mode === "ai" ? `AI decides from ${s.input || upstream || "?"}` : `${(s.exits ?? []).length} rule(s)`;
      return s.max_rounds ? `${base} · at most ${s.max_rounds} rounds` : base;
    }
    case "ask_human": {
      const question = String(s.question ?? "").replace(/\s+/g, " ").trim();
      return question || "Asks a person";
    }
    case "for_each":
      return `Each ${s.item_name || "item"} of ${s.items || upstream || "?"} → ${s.save_as}${s.concurrency ? ` · ${s.concurrency} at a time` : ""}`;
    case "subflow":
      return s.flow ? `Runs “${s.flow}”${s.share_data ? " on this flow's data" : ""}` : "Pick a flow to run";
    case "jump": {
      const updates = (s.updates ?? []) as { field: string }[];
      return updates.length ? `Sets ${updates.map((u) => u.field).join(", ")}` : "Picks the next step";
    }
    default:
      return "";
  }
}
