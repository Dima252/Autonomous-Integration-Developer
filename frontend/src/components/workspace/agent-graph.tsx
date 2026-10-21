"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import {
  BookOpen,
  Brain,
  Compass,
  FileCheck,
  FlaskConical,
  GraduationCap,
  Hammer,
  Package,
  Search,
  ShieldCheck,
  Stethoscope,
  X,
  type LucideIcon,
} from "lucide-react";
import { NODE_H, NODE_W, PROVIDER_NAMES, agentFor, layout, type Stage } from "@/lib/agents";
import { clock, cx } from "@/lib/format";
import type { NodeStatus, RunView } from "@/lib/run-state";
import { StatusIcon } from "../ui";

const ICONS: Record<string, LucideIcon> = {
  analyst: Compass,
  architect: Brain,
  researcher: Search,
  contract: FileCheck,
  test_author: FlaskConical,
  coder: Hammer,
  validator: ShieldCheck,
  diagnostician: Stethoscope,
  reviewer: BookOpen,
  packager: Package,
  memory_curator: GraduationCap,
};

type AgentData = {
  nodeId: string;
  status: NodeStatus;
  label?: string;
  runs: number;
  selected: boolean;
};

type BandData = { title: string; subtitle: string; width: number; height: number };

function AgentNode({ data }: NodeProps<Node<AgentData, "agent">>) {
  const meta = agentFor(data.nodeId);
  const Icon = ICONS[meta.id] ?? Hammer;
  const name = meta.provider ? PROVIDER_NAMES[meta.provider] ?? meta.provider : meta.name;
  return (
    <div
      style={{ width: NODE_W, height: NODE_H }}
      className={cx(
        "flex cursor-pointer items-center gap-2.5 rounded-xl border bg-panel px-3 transition-[border-color,box-shadow,opacity] duration-300",
        data.status === "idle" && "border-border opacity-60",
        data.status === "active" && "pulse-ring border-accent",
        data.status === "done" && "border-border-strong",
        data.status === "failed" && "border-fail",
        data.selected && "ring-2 ring-accent ring-offset-2 ring-offset-bg",
      )}
    >
      <Handle type="target" position={Position.Left} id="l" className="!opacity-0" />
      <Handle type="source" position={Position.Right} id="r" className="!opacity-0" />
      <Handle type="target" position={Position.Top} id="t" className="!opacity-0" />
      <Handle type="source" position={Position.Bottom} id="b" className="!opacity-0" />
      <Handle type="target" position={Position.Bottom} id="bt" className="!opacity-0" />
      <span
        className={cx(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
          data.status === "active" ? "bg-accent-soft text-accent" : "bg-raised text-muted",
        )}
      >
        <Icon size={16} aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5 text-[13px] font-medium text-text">
          <span className="truncate">{meta.provider ? `${name} research` : name}</span>
          {data.runs > 1 && (
            <span className="rounded bg-raised px-1 font-mono text-[10px] text-muted" title={`Ran ${data.runs} times`}>
              ×{data.runs}
            </span>
          )}
        </span>
        <span className="block truncate text-[11px] text-muted">{data.label ?? meta.role}</span>
      </span>
      <StatusIcon status={data.status} size={14} />
    </div>
  );
}

function BandNode({ data }: NodeProps<Node<BandData, "band">>) {
  return (
    <div
      style={{ width: data.width, height: data.height }}
      className="relative rounded-2xl border border-dashed border-border bg-raised/40"
    >
      <span title={data.subtitle} className="absolute bottom-2 left-3 text-[10px] font-semibold uppercase tracking-wider text-faint">
        {data.title}
      </span>
    </div>
  );
}

const nodeTypes = { agent: AgentNode, band: BandNode };

const BANDS: { stage: Stage; title: string; subtitle: string; extra: number }[] = [
  { stage: "plan", title: "Plan", subtitle: "understand, design, research, ground", extra: 0 },
  // Room below the row for the diagnostician's loop back to the coder.
  { stage: "build", title: "Build & heal", subtitle: "test first, code, validate, diagnose", extra: 34 },
  { stage: "ship", title: "Ship", subtitle: "review, package, learn", extra: 0 },
];

function buildGraph(run: RunView, selected: string | null) {
  const researchers = run.researchers.length
    ? run.researchers
    : run.systems.filter((s) => s !== "other").map((s) => `researcher:${s}`);
  const pos = layout(researchers);
  const ids = Object.keys(pos);
  const width = Math.max(...ids.map((id) => pos[id].x)) + NODE_W + 48;

  const nodes: Node[] = BANDS.map((b) => {
    const ys = ids.filter((id) => agentFor(id).stage === b.stage).map((id) => pos[id].y);
    const top = Math.min(...ys) - 26;
    const height = Math.max(...ys) + NODE_H + 18 + b.extra - top;
    return {
      id: `band-${b.stage}`,
      type: "band",
      position: { x: -24, y: top },
      data: { title: b.title, subtitle: b.subtitle, width, height },
      draggable: false,
      selectable: false,
      focusable: false,
      zIndex: -1,
    };
  });
  for (const id of ids) {
    const view = run.nodes[id];
    nodes.push({
      id,
      type: "agent",
      position: pos[id],
      data: {
        nodeId: id,
        status: view?.status ?? "idle",
        label: view?.label,
        runs: view?.runs ?? 0,
        selected: selected === id,
      } satisfies AgentData,
      draggable: false,
    });
  }

  const status = (id: string) => run.nodes[id]?.status ?? "idle";
  // `extra` may carry smooth-step options (pathOptions), which the base Edge type omits.
  const edge = (source: string, target: string, sh: string, th: string, extra: Record<string, unknown> = {}): Edge => {
    const live = status(target) === "active" && status(source) !== "idle";
    const traversed = status(source) !== "idle" && status(target) !== "idle";
    const color = live ? "var(--accent)" : traversed ? "var(--border-strong)" : "var(--border)";
    return {
      id: `${source}->${target}`,
      source,
      target,
      sourceHandle: sh,
      targetHandle: th,
      type: "smoothstep",
      animated: live,
      style: { stroke: color, strokeWidth: live ? 2 : 1.5, strokeDasharray: traversed || live ? undefined : "4 4" },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 16, height: 16 },
      ...extra,
    } as Edge;
  };

  const fixes = Math.max(0, run.iterations.length - 1);
  const edges: Edge[] = [
    edge("analyst", "architect", "r", "l"),
    ...researchers.map((r) => edge("architect", r, "r", "l")),
    ...researchers.map((r) => edge(r, "contract", "r", "l")),
    edge("contract", "test_author", "b", "t"),
    edge("test_author", "coder", "r", "l"),
    edge("coder", "validator", "r", "l"),
    edge("validator", "diagnostician", "r", "l", { label: "fail", labelStyle: { fill: "var(--faint)", fontSize: 10 }, labelBgStyle: { fill: "var(--bg)" } }),
    edge("diagnostician", "coder", "b", "bt", {
      label: fixes ? `↺ ${fixes} fix${fixes > 1 ? "es" : ""}` : "fix",
      labelStyle: { fill: fixes ? "var(--warn)" : "var(--faint)", fontSize: 11, fontWeight: 600 },
      labelBgStyle: { fill: "var(--bg)" },
      labelBgPadding: [6, 3],
      pathOptions: { offset: 28, borderRadius: 12 },
    }),
    edge("validator", "reviewer", "b", "t", { label: "pass", labelStyle: { fill: "var(--faint)", fontSize: 10 }, labelBgStyle: { fill: "var(--bg)" } }),
    edge("reviewer", "packager", "r", "l"),
    edge("packager", "memory_curator", "r", "l"),
  ];
  return { nodes, edges };
}

function Graph({ run, selected, onSelect }: { run: RunView; selected: string | null; onSelect: (id: string | null) => void }) {
  const { nodes, edges } = useMemo(() => buildGraph(run, selected), [run, selected]);
  const { fitView } = useReactFlow();
  const box = useRef<HTMLDivElement>(null);
  const researcherCount = run.researchers.length;

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const fit = () => fitView({ padding: 0.08, duration: 200 });
    const observer = new ResizeObserver(fit);
    observer.observe(el);
    return () => observer.disconnect();
  }, [fitView]);

  useEffect(() => {
    void fitView({ padding: 0.08, duration: 300 });
  }, [researcherCount, fitView]);

  return (
    <div ref={box} className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.08 }}
        minZoom={0.3}
        maxZoom={1.6}
        nodesDraggable={false}
        nodesConnectable={false}
        onNodeClick={(_, n) => n.type === "agent" && onSelect(n.id === selected ? null : n.id)}
        onPaneClick={() => onSelect(null)}
        attributionPosition="top-left"
      >
        <Background gap={20} size={1} color="var(--border)" />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  );
}

export function AgentGraph({ run }: { run: RunView }) {
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <div className="relative h-full">
      <ReactFlowProvider>
        <Graph run={run} selected={selected} onSelect={setSelected} />
      </ReactFlowProvider>
      <Legend />
      {selected && <Inspector run={run} nodeId={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function Legend() {
  const items: { status: NodeStatus; label: string }[] = [
    { status: "idle", label: "Not started" },
    { status: "active", label: "Working" },
    { status: "done", label: "Done" },
    { status: "failed", label: "Failed" },
  ];
  return (
    <ul className="absolute bottom-3 left-3 flex flex-wrap gap-3 rounded-lg border border-border bg-panel/90 px-3 py-1.5 text-[11px] text-muted backdrop-blur">
      {items.map((i) => (
        <li key={i.status} className="flex items-center gap-1.5">
          <StatusIcon status={i.status} size={12} /> {i.label}
        </li>
      ))}
    </ul>
  );
}

function Inspector({ run, nodeId, onClose }: { run: RunView; nodeId: string; onClose: () => void }) {
  const meta = agentFor(nodeId);
  const view = run.nodes[nodeId];
  const events = run.events.filter((e) => e.node === nodeId);
  const start = run.startedAt ?? 0;
  return (
    <aside
      aria-label={`${meta.name} details`}
      className="absolute right-3 top-3 flex max-h-[calc(100%-1.5rem)] w-80 flex-col overflow-hidden rounded-xl border border-border bg-panel shadow-xl"
    >
      <header className="flex items-start justify-between gap-2 border-b border-border p-3">
        <div>
          <p className="flex items-center gap-1.5 text-sm font-semibold text-text">
            <StatusIcon status={view?.status ?? "idle"} size={14} />
            {meta.name}
            {meta.provider && <span className="text-muted">· {PROVIDER_NAMES[meta.provider] ?? meta.provider}</span>}
          </p>
          <p className="mt-0.5 text-xs text-muted">{meta.role}</p>
        </div>
        <button onClick={onClose} aria-label="Close details" className="rounded p-1 text-muted hover:bg-hover hover:text-text">
          <X size={14} />
        </button>
      </header>
      <ol className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {events.length === 0 && <li className="text-xs text-faint">Nothing yet.</li>}
        {events.map((e) => (
          <li key={e.seq} className="flex gap-2 text-xs">
            <span className="w-9 shrink-0 font-mono text-faint">{clock(Date.parse(e.ts) - start)}</span>
            <span className="text-muted">{e.label}</span>
          </li>
        ))}
      </ol>
    </aside>
  );
}
