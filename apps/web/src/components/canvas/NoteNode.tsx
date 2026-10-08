import { NodeResizer, type NodeProps } from "@xyflow/react";
import { memo } from "react";
import { clone } from "../../lib/spec";
import { useFlow } from "../../state/flow";
import { useDraft } from "../ui";

export const NoteNode = memo(function NoteNode({ id, selected }: NodeProps) {
  const noteId = id.slice("note:".length);
  const note = useFlow((s) => s.spec?.canvas.notes.find((n) => n.id === noteId));
  const apply = useFlow((s) => s.apply);
  // What is being typed; it follows the saved text when that changes (undo, redo).
  const [draft, setDraft] = useDraft(note?.text ?? "");
  if (!note) return null;
  const update = (patch: Partial<typeof note>) =>
    apply((spec) => {
      const next = clone(spec);
      const target = next.canvas.notes.find((n) => n.id === noteId);
      if (target) Object.assign(target, patch);
      return next;
    });
  return (
    <div className="h-full w-full rounded-lg border border-yellow-300/70 bg-yellow-100/90 p-2 shadow-sm dark:border-yellow-600/40 dark:bg-yellow-900/40">
      <NodeResizer
        isVisible={selected}
        minWidth={140}
        minHeight={70}
        onResizeEnd={(_, p) => update({ width: Math.round(p.width), height: Math.round(p.height), x: Math.round(p.x), y: Math.round(p.y) })}
      />
      <textarea
        aria-label="Sticky note"
        className="nodrag h-full w-full resize-none bg-transparent text-xs leading-relaxed text-yellow-950 outline-none placeholder:text-yellow-700/60 dark:text-yellow-100"
        placeholder="Write a note…"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => draft !== note.text && update({ text: draft })}
      />
    </div>
  );
});
