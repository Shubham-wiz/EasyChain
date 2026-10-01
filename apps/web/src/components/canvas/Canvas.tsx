import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
  type Connection as RFConnection,
  type Edge,
  type EdgeChange,
  type FinalConnectionState,
  type Node,
  type NodeChange,
  type OnSelectionChangeParams,
} from "@xyflow/react";
import { MousePointerClick } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { addStep, canConnect, clone, connect, createStep, getStep, moveSteps, removeConnection, removeSteps, NODE_WIDTH } from "../../lib/spec";
import type { Connection, FlowSpec, Position } from "../../lib/types";
import { useCatalog } from "../../state/catalog";
import { useCheck } from "../../state/check";
import { useFlow } from "../../state/flow";
import { useRun } from "../../state/run";
import { useUi } from "../../state/ui";
import { FlowEdge } from "./FlowEdge";
import { NoteNode } from "./NoteNode";
import { DecisionNode, StepNode } from "./StepNode";
import { colorsFor, iconFor } from "./stepMeta";

const nodeTypes = { step: StepNode, decision: DecisionNode, note: NoteNode };
const edgeTypes = { flow: FlowEdge };

export const STEP_MIME = "application/easychain-step";

export function edgeId(c: Connection): string {
  return `${c.from}->${c.to}${c.exit != null ? `:${c.exit}` : ""}`;
}

function syncNodes(prev: Node[], spec: FlowSpec, selected: Set<string>): Node[] {
  const byId = new Map(prev.map((n) => [n.id, n]));
  const out: Node[] = [];
  spec.steps.forEach((step, i) => {
    const position = spec.canvas.steps[step.id] ?? { x: (i % 5) * (NODE_WIDTH + 60), y: Math.floor(i / 5) * 160 };
    const type = step.type === "decision" ? "decision" : "step";
    const old = byId.get(step.id);
    const isSelected = selected.has(step.id);
    if (old && old.type === type && old.position.x === position.x && old.position.y === position.y && !!old.selected === isSelected && !old.dragging) {
      out.push(old);
    } else {
      out.push({ ...(old ?? {}), id: step.id, type, position, data: {}, selected: isSelected, dragging: false });
    }
  });
  for (const note of spec.canvas.notes ?? []) {
    const id = `note:${note.id}`;
    const old = byId.get(id);
    out.push({
      ...(old ?? {}),
      id,
      type: "note",
      position: { x: note.x, y: note.y },
      data: {},
      style: { width: note.width, height: note.height },
      zIndex: -1,
      selected: old?.selected ?? false,
    });
  }
  return out;
}

function syncEdges(prev: Edge[], spec: FlowSpec): Edge[] {
  const selected = new Set(prev.filter((e) => e.selected).map((e) => e.id));
  return spec.connections
    .filter((c) => getStep(spec, c.from) && getStep(spec, c.to))
    .map((c) => {
      const source = getStep(spec, c.from)!;
      const id = edgeId(c);
      return {
        id,
        source: c.from,
        target: c.to,
        sourceHandle: source.type === "decision" && c.exit != null ? `exit:${c.exit}` : null,
        type: "flow",
        data: { exit: source.type === "decision" ? c.exit : null },
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        selected: selected.has(id),
      };
    });
}

interface QuickAdd {
  screen: { x: number; y: number };
  flow: Position;
  from?: { step: string; exit: string | null };
}

export function Canvas() {
  const spec = useFlow((s) => s.spec)!;
  const apply = useFlow((s) => s.apply);
  const selected = useUi((s) => s.selected);
  const select = useUi((s) => s.select);
  const theme = useUi((s) => s.theme);
  const mode = useUi((s) => s.mode);
  const catalog = useCatalog((s) => s.catalog);
  const fields = useCheck((s) => s.analysis?.fields);
  const { screenToFlowPosition } = useReactFlow();
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [quickAdd, setQuickAdd] = useState<QuickAdd | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);

  const selectedSet = useMemo(() => new Set(selected), [selected]);
  useEffect(() => setNodes((prev) => syncNodes(prev, spec, selectedSet)), [spec, selectedSet]);
  useEffect(() => setEdges((prev) => syncEdges(prev, spec)), [spec]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(t);
  }, [toast]);

  const onNodesChange = useCallback((changes: NodeChange[]) => setNodes((nds) => applyNodeChanges(changes, nds)), []);
  const onEdgesChange = useCallback((changes: EdgeChange[]) => setEdges((eds) => applyEdgeChanges(changes, eds)), []);

  const onNodeDragStop = useCallback(
    (_: unknown, __: Node, dragged: Node[]) => {
      const steps: Record<string, Position> = {};
      const notes: Record<string, Position> = {};
      for (const n of dragged) {
        if (n.id.startsWith("note:")) notes[n.id.slice(5)] = n.position;
        else steps[n.id] = n.position;
      }
      apply((s) => {
        let next = moveSteps(s, steps);
        if (Object.keys(notes).length) {
          next = clone(next);
          for (const note of next.canvas.notes) {
            const p = notes[note.id];
            if (p) Object.assign(note, { x: Math.round(p.x), y: Math.round(p.y) });
          }
        }
        return next;
      });
    },
    [apply],
  );

  const onSelectionChange = useCallback(
    ({ nodes: sel }: OnSelectionChangeParams) => select(sel.filter((n) => !n.id.startsWith("note:")).map((n) => n.id)),
    [select],
  );

  const onNodesDelete = useCallback(
    (deleted: Node[]) => {
      const steps = deleted.filter((n) => !n.id.startsWith("note:")).map((n) => n.id);
      const notes = new Set(deleted.filter((n) => n.id.startsWith("note:")).map((n) => n.id.slice(5)));
      apply((s) => {
        let next = steps.length ? removeSteps(s, steps) : s;
        if (notes.size) {
          next = clone(next);
          next.canvas.notes = next.canvas.notes.filter((n) => !notes.has(n.id));
        }
        return next;
      });
      select([]);
    },
    [apply, select],
  );

  const onEdgesDelete = useCallback(
    (deleted: Edge[]) =>
      apply((s) => {
        let next = s;
        for (const e of deleted) {
          const exit = e.sourceHandle?.startsWith("exit:") ? e.sourceHandle.slice(5) : null;
          next = removeConnection(next, { from: e.source, to: e.target, exit });
        }
        return next;
      }),
    [apply],
  );

  const exitOf = (handle: string | null | undefined) => (handle?.startsWith("exit:") ? handle.slice(5) : null);

  const onConnect = useCallback(
    (c: RFConnection) => apply((s) => connect(s, c.source, c.target, exitOf(c.sourceHandle))),
    [apply],
  );

  const isValidConnection = useCallback(
    (c: RFConnection | Edge) => canConnect(spec, c.source, c.target, exitOf(c.sourceHandle)) === null,
    [spec],
  );

  const onConnectEnd = useCallback(
    (event: MouseEvent | TouchEvent, state: FinalConnectionState) => {
      if (state.isValid || !state.fromNode) return;
      if (state.toNode) {
        const reason = canConnect(spec, state.fromNode.id, state.toNode.id, exitOf(state.fromHandle?.id));
        if (reason) setToast(reason);
        return;
      }
      // Dropped on empty canvas: offer to add a step there, already connected.
      const point = "changedTouches" in event ? event.changedTouches[0] : event;
      const rect = wrapper.current?.getBoundingClientRect();
      setQuickAdd({
        screen: { x: point.clientX - (rect?.left ?? 0), y: point.clientY - (rect?.top ?? 0) },
        flow: screenToFlowPosition({ x: point.clientX, y: point.clientY - 40 }),
        from: { step: state.fromNode.id, exit: exitOf(state.fromHandle?.id) },
      });
    },
    [spec, screenToFlowPosition],
  );

  const addAt = useCallback(
    (type: string, position: Position, from?: { step: string; exit: string | null }) => {
      const info = catalog?.steps.find((s) => s.type === type);
      if (!info) return;
      const step = createStep(info, useFlow.getState().spec!, fields?.map((f) => f.name));
      apply((s) => addStep(s, step, position, from));
      select([step.id]);
    },
    [catalog, apply, select, fields],
  );

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const type = event.dataTransfer.getData(STEP_MIME);
      if (!type) return;
      const pos = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      addAt(type, { x: Math.round(pos.x - NODE_WIDTH / 2), y: Math.round(pos.y - 40) });
    },
    [addAt, screenToFlowPosition],
  );

  const onlyEnds = spec.steps.every((s) => s.type === "input" || s.type === "output");
  const quickTypes = (catalog?.steps ?? []).filter((s) => s.type !== "input" && (mode === "pro" || s.beginner));

  return (
    <div ref={wrapper} className="relative h-full w-full" data-testid="canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeDragStop={onNodeDragStop}
        onSelectionChange={onSelectionChange}
        onNodesDelete={onNodesDelete}
        onEdgesDelete={onEdgesDelete}
        onConnect={onConnect}
        onConnectEnd={onConnectEnd}
        isValidConnection={isValidConnection}
        onDrop={onDrop}
        onDragOver={(e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
        }}
        onPaneClick={() => setQuickAdd(null)}
        onNodeClick={() => {
          if (useRun.getState().status !== "running") useUi.getState().setRightTab("inspect");
        }}
        colorMode={theme}
        fitView
        fitViewOptions={{ padding: 0.15, minZoom: 0.7, maxZoom: 1.1 }}
        minZoom={0.1}
        maxZoom={2}
        snapToGrid
        snapGrid={[10, 10]}
        deleteKeyCode={["Backspace", "Delete"]}
        multiSelectionKeyCode={["Meta", "Control", "Shift"]}
        onlyRenderVisibleElements={spec.steps.length > 150}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1.4} color="var(--dot)" />
        <Controls showInteractive={false} position="bottom-left" />
        <MiniMap pannable zoomable position="bottom-right" className="!rounded-lg !border !border-border" nodeBorderRadius={8} />
      </ReactFlow>

      {onlyEnds && (
        <div className="pointer-events-none absolute inset-x-0 top-6 flex justify-center">
          <div className="flex items-center gap-2 rounded-full border border-border bg-surface/95 px-4 py-2 text-sm text-muted shadow-sm">
            <MousePointerClick size={15} className="text-accent" />
            Drag steps from the left onto the canvas, then connect the dots from Input to Output.
          </div>
        </div>
      )}

      {toast && (
        <div role="status" className="absolute top-4 left-1/2 z-10 -translate-x-1/2 rounded-lg bg-text px-3 py-2 text-sm text-bg shadow-lg">
          {toast}
        </div>
      )}

      {quickAdd && (
        <div
          role="menu"
          aria-label="Add a step here"
          className="absolute z-20 w-56 rounded-lg border border-border bg-surface p-1 shadow-xl"
          style={{ left: quickAdd.screen.x, top: quickAdd.screen.y }}
        >
          <p className="px-2 py-1 text-[11px] font-medium text-faint">Add a step here</p>
          {quickTypes.map((info) => {
            const Icon = iconFor(info.icon);
            return (
              <button
                key={info.type}
                role="menuitem"
                type="button"
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-surface-2"
                onClick={() => {
                  addAt(info.type, quickAdd.flow, quickAdd.from);
                  setQuickAdd(null);
                }}
              >
                <span className={`flex h-6 w-6 items-center justify-center rounded ${colorsFor(info.category).chip}`}>
                  <Icon size={13} />
                </span>
                {info.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
