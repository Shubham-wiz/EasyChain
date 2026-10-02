// Sources from a Knowledge Base search, and answers that cite them like [1].

import { ExternalLink, FileText } from "lucide-react";
import { Fragment, type ReactNode } from "react";
import type { KnowledgeHit } from "../../lib/types";

/** Passage text for reading: Markdown heading lines go (the heading is shown on its own) and blank runs collapse. */
export function passageText(text: string): string {
  return text
    .split("\n")
    .filter((line) => !/^\s{0,3}#{1,6}\s/.test(line))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** A list that looks like Knowledge Base sources (numbered passages with where they came from). */
export function isSources(value: unknown): value is KnowledgeHit[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((v) => v && typeof v === "object" && "n" in v && "text" in v && ("source" in v || "title" in v))
  );
}

/** Text with [1]-style markers turned into links to the matching source card. */
export function CitedText({ text, sources, prefix = "source" }: { text: string; sources: KnowledgeHit[] | null; prefix?: string }) {
  if (!sources?.length) return <>{text}</>;
  const known = new Set(sources.map((s) => s.n));
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(/\[(\d+)\]/g)) {
    const n = Number(match[1]);
    if (!known.has(n)) continue;
    parts.push(text.slice(last, match.index));
    parts.push(
      <a
        key={`${match.index}-${n}`}
        href={`#${prefix}-${n}`}
        onClick={(e) => {
          e.preventDefault();
          document.getElementById(`${prefix}-${n}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }}
        className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-accent-soft px-1 align-text-top text-[10px] font-semibold text-accent no-underline hover:bg-accent hover:text-accent-text"
        aria-label={`Source ${n}`}
      >
        {n}
      </a>,
    );
    last = (match.index ?? 0) + match[0].length;
  }
  parts.push(text.slice(last));
  return (
    <>
      {parts.map((p, i) => (
        <Fragment key={i}>{p}</Fragment>
      ))}
    </>
  );
}

/** Numbered source cards: title, where it came from, and the passage. */
export function SourceList({ sources, prefix = "source" }: { sources: KnowledgeHit[]; prefix?: string }) {
  return (
    <ol className="space-y-1.5" aria-label="Sources" data-testid="sources">
      {sources.map((s) => {
        const link = s.source && /^https?:\/\//.test(s.source) ? s.source : null;
        return (
          <li key={`${s.n}-${s.id}`} id={`${prefix}-${s.n}`} className="rounded-lg border border-border bg-surface px-2.5 py-2 text-xs">
            <div className="flex items-start gap-2">
              <span className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded bg-accent-soft px-1 text-[10px] font-semibold text-accent">{s.n}</span>
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">
                  {s.title || "Untitled"}
                  {s.heading && s.heading !== s.title && <span className="text-muted"> › {s.heading}</span>}
                </p>
                <p className="flex items-center gap-1 text-[11px] text-faint">
                  <FileText size={10} />
                  {link ? (
                    <a href={link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 truncate text-accent hover:underline">
                      {link} <ExternalLink size={9} />
                    </a>
                  ) : (
                    <span className="truncate">{s.source}</span>
                  )}
                  {s.page ? <span>· page {s.page}</span> : null}
                  {s.matched && <span>· matched by {s.matched === "both" ? "meaning and words" : s.matched}</span>}
                </p>
                <p className="mt-1 line-clamp-3 text-muted">{passageText(s.text)}</p>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
