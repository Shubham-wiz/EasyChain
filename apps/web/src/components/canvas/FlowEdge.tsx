import { BaseEdge, EdgeLabelRenderer, getBezierPath, useReactFlow, type EdgeProps } from "@xyflow/react";
import { X } from "lucide-react";
import { memo } from "react";
import { cn } from "../../lib/utils";
import { useRun } from "../../state/run";

export interface FlowEdgeData extends Record<string, unknown> {
  exit?: string | null;
  /** Drawn by Easy Chain (For Each results), not a connection in the flow. */
  virtual?: boolean;
  /** An Agent's tool (deleting it takes the step off the agent's tools). */
  tool?: boolean;
  label?: string;
}

export const FlowEdge = memo(function FlowEdge(props: EdgeProps) {
  const { id, source, target, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, selected, markerEnd } = props;
  const data = props.data as FlowEdgeData | undefined;
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const sourceRun = useRun((s) => s.steps[source]);
  const targetRun = useRun((s) => s.steps[target]);
  const sourceExit = sourceRun?.exit;
  const { deleteElements } = useReactFlow();
  const taken = data?.exit ? sourceExit === data.exit : true;
  // A tool's line lights up while the agent is calling it.
  const toolCalling = data?.tool && targetRun?.status === "running" && (targetRun.tools ?? []).some((t) => t.tool === source && t.status === "running");
  const flowing = (sourceRun?.status === "done" && targetRun?.status === "running" && taken && !data?.tool) || toolCalling;
  const done = sourceRun?.status === "done" && targetRun && targetRun.status !== "running" && taken;
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        className={cn(flowing && "edge-flowing", done && "edge-done")}
        style={{ strokeWidth: selected ? 2.5 : 1.75 }}
      />
      {(data?.virtual || data?.tool) && data.label && !selected && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan pointer-events-none absolute rounded-full bg-surface px-1.5 py-px text-[10px] text-faint italic"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
      {!data?.virtual && (data?.exit || selected) && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan pointer-events-auto absolute flex items-center gap-1"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            {data?.exit && (
              <span className="rounded-full border border-border bg-surface px-1.5 py-px text-[10px] text-muted">{data.exit}</span>
            )}
            {selected && (
              <button
                type="button"
                className="flex h-5 w-5 items-center justify-center rounded-full border border-border bg-surface text-muted shadow hover:text-danger"
                aria-label="Delete connection"
                onClick={() => deleteElements({ edges: [{ id }] })}
              >
                <X size={11} />
              </button>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
});
