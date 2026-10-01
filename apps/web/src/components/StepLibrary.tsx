import { Search, StickyNote } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { addStep, clone, createStep, exitLabels, getStep, nextFreePosition, NODE_WIDTH } from "../lib/spec";
import type { StepTypeInfo } from "../lib/types";
import { cn } from "../lib/utils";
import { useCatalog } from "../state/catalog";
import { useCheck } from "../state/check";
import { useFlow } from "../state/flow";
import { useUi } from "../state/ui";
import { STEP_MIME } from "./canvas/Canvas";
import { colorsFor, iconFor } from "./canvas/stepMeta";
import { Input, Tooltip } from "./ui";

/** Add a step: after the selected step (connected) when there is one, otherwise in a free spot. */
export function addStepFromLibrary(info: StepTypeInfo) {
  const { spec, apply } = useFlow.getState();
  if (!spec) return;
  const selectedId = useUi.getState().selected[0];
  const after = selectedId ? getStep(spec, selectedId) : undefined;
  const fields = useCheck.getState().analysis?.fields.map((f) => f.name) ?? [];
  const step = createStep(info, spec, fields);
  let position = nextFreePosition(spec);
  let from: { step: string; exit: string | null } | undefined;
  if (after && after.type !== "output" && info.type !== "input") {
    const base = spec.canvas.steps[after.id] ?? { x: 0, y: 0 };
    position = { x: base.x + NODE_WIDTH + 70, y: base.y };
    if (after.type === "decision") {
      const used = new Set(spec.connections.filter((c) => c.from === after.id).map((c) => c.exit));
      const free = exitLabels(after).find((l) => !used.has(l));
      if (free) from = { step: after.id, exit: free };
    } else {
      // Re-route: if the selected step led somewhere (e.g. Output), put the new step in between.
      from = { step: after.id, exit: null };
    }
  }
  apply((s) => {
    const next = addStep(s, step, position, from);
    if (from && after && after.type !== "decision") {
      const outgoing = next.connections.filter((c) => c.from === after.id && c.to !== step.id);
      if (outgoing.length === 1 && info.type !== "output") {
        const moved = clone(next);
        const conn = moved.connections.find((c) => c.from === after.id && c.to === outgoing[0].to)!;
        conn.from = step.id;
        // Shift the old target right so the new step fits.
        const target = moved.canvas.steps[outgoing[0].to];
        if (target && target.x <= position.x + NODE_WIDTH) moved.canvas.steps[outgoing[0].to] = { x: position.x + NODE_WIDTH + 70, y: target.y };
        return moved;
      }
    }
    return next;
  });
  useUi.getState().select([step.id]);
}

function addNote() {
  const { spec, apply } = useFlow.getState();
  if (!spec) return;
  const pos = nextFreePosition(spec);
  apply((s) => {
    const next = clone(s);
    next.canvas.notes.push({ id: `n${Date.now().toString(36)}`, text: "", x: pos.x, y: pos.y - 160, width: 220, height: 120 });
    return next;
  });
}

export function StepLibrary() {
  const catalog = useCatalog((s) => s.catalog);
  const mode = useUi((s) => s.mode);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  const groups = useMemo(() => {
    if (!catalog) return [];
    const q = query.trim().toLowerCase();
    return catalog.categories
      .map((cat) => ({
        ...cat,
        steps: catalog.steps.filter(
          (s) =>
            s.category === cat.id &&
            (mode === "pro" || s.beginner) &&
            (!q || `${s.label} ${s.summary} ${s.technical}`.toLowerCase().includes(q)),
        ),
      }))
      .filter((g) => g.steps.length);
  }, [catalog, query, mode]);

  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-border bg-surface" aria-label="Step library">
      <div className="border-b border-border p-3">
        <div className="relative">
          <Search size={14} className="pointer-events-none absolute top-2.5 left-2.5 text-faint" />
          <Input
            ref={searchRef}
            id="library-search"
            placeholder="Search steps"
            aria-label="Search steps"
            className="pl-8"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>
      <div className="scroll-thin flex-1 space-y-4 overflow-y-auto p-3">
        {groups.map((group) => (
          <section key={group.id}>
            <h3 className="mb-1.5 px-1 text-[11px] font-semibold tracking-wide text-faint uppercase">{group.label}</h3>
            <ul className="space-y-1">
              {group.steps.map((info) => {
                const Icon = iconFor(info.icon);
                return (
                  <li key={info.type}>
                    <Tooltip content={info.summary} side="right">
                      <button
                        type="button"
                        draggable
                        data-testid={`library-${info.type}`}
                        onDragStart={(e) => {
                          e.dataTransfer.setData(STEP_MIME, info.type);
                          e.dataTransfer.effectAllowed = "move";
                        }}
                        onClick={() => addStepFromLibrary(info)}
                        className="group flex w-full cursor-grab items-center gap-2.5 rounded-lg border border-transparent px-2 py-1.5 text-left hover:border-border hover:bg-surface-2 active:cursor-grabbing"
                      >
                        <span className={cn("flex h-7 w-7 shrink-0 items-center justify-center rounded-lg", colorsFor(info.category).chip)}>
                          <Icon size={14} />
                        </span>
                        <span className="min-w-0">
                          <span className="block text-[13px] font-medium">{info.label}</span>
                          {mode === "pro" && <span className="block truncate font-mono text-[10px] text-faint">{info.technical}</span>}
                        </span>
                      </button>
                    </Tooltip>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
        {!groups.length && <p className="px-1 text-sm text-muted">No steps match “{query}”.</p>}
      </div>
      <div className="border-t border-border p-3">
        <button
          type="button"
          onClick={addNote}
          className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-muted hover:bg-surface-2 hover:text-text"
        >
          <StickyNote size={14} /> Add a sticky note
        </button>
        <p className="mt-2 px-1 text-[11px] leading-relaxed text-faint">
          Drag a step onto the canvas, or click it to add it after the selected step.
        </p>
      </div>
    </aside>
  );
}
