import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatMs(ms: number | undefined | null): string {
  if (ms == null) return "";
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

export function formatCost(cost: number | undefined | null): string {
  if (cost == null) return "";
  if (cost === 0) return "$0";
  if (cost < 0.0001) return "<$0.0001";
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(3)}`;
}

export function formatTokens(n: number | undefined): string {
  if (!n) return "0";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

export function timeAgo(seconds: number): string {
  const diff = Date.now() / 1000 - seconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return new Date(seconds * 1000).toLocaleDateString();
}

/** Short text preview of any value for badges and traces. */
export function preview(value: unknown, max = 160): string {
  if (value == null) return "";
  let text: string;
  if (typeof value === "string") text = value;
  else if (Array.isArray(value) && value.every((v) => v && typeof v === "object" && "content" in v))
    text = value.map((m) => `${(m as { role: string }).role}: ${(m as { content: string }).content}`).join("\n");
  else text = JSON.stringify(value);
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

export function isMac(): boolean {
  return typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
}

export const modKey = () => (isMac() ? "⌘" : "Ctrl");
