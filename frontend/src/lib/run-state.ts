// Folds a run's events into what the workspace shows. Pure, so the replay scrubber
// can rebuild the view at any point by folding a prefix of the events.

import type { CheckName, CheckStatus, RunEvent } from "./types";

export type NodeStatus = "idle" | "active" | "done" | "failed";

export interface NodeView {
  status: NodeStatus;
  label?: string; // latest step or thought label
  runs: number; // how many times the step has started
}

export interface CheckResult {
  status: CheckStatus;
  summary: string;
  output?: string;
}

export interface Hit {
  title: string;
  url: string | null;
  breadcrumb: string;
  provider: string;
  doc_type: string;
}

export interface Retrieval {
  node: string;
  query: string;
  hits: Hit[];
}

export interface Assumption {
  text: string;
  reason: string;
  overridable: boolean;
}

export interface Decision {
  decision: string;
  alternatives: string[];
  rationale: string;
}

export interface Iteration {
  n: number;
  reason: string;
}

export type RunStatus = "idle" | "running" | "completed" | "failed";

export interface RunView {
  runId: string | null;
  status: RunStatus;
  request: string;
  systems: string[];
  nodes: Record<string, NodeView>;
  researchers: string[];
  activeNode: string | null;
  iterations: Iteration[];
  /** Files as of each iteration (full snapshots; later iterations inherit). */
  files: Record<number, Record<string, string>>;
  checks: Record<number, Partial<Record<CheckName, CheckResult>>>;
  retrievals: Retrieval[];
  assumptions: Assumption[];
  decisions: Decision[];
  lessons: string[];
  questions: string[];
  costUsd: number;
  startedAt: number | null;
  endedAt: number | null;
  lastAt: number | null;
  summary: string | null;
  failure: string | null;
  events: RunEvent[];
}

export function emptyRun(runId: string | null = null): RunView {
  return {
    runId,
    status: "idle",
    request: "",
    systems: [],
    nodes: {},
    researchers: [],
    activeNode: null,
    iterations: [],
    files: {},
    checks: {},
    retrievals: [],
    assumptions: [],
    decisions: [],
    lessons: [],
    questions: [],
    costUsd: 0,
    startedAt: null,
    endedAt: null,
    lastAt: null,
    summary: null,
    failure: null,
    events: [],
  };
}

const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
const num = (v: unknown, fallback = 0): number => (typeof v === "number" ? v : fallback);

export function foldRun(events: readonly RunEvent[], runId: string | null = null): RunView {
  const view = emptyRun(runId ?? events[0]?.run_id ?? null);
  for (const e of events) apply(view, e);
  return view;
}

function node(view: RunView, id: string): NodeView {
  if (!view.nodes[id]) view.nodes[id] = { status: "idle", runs: 0 };
  if (id.startsWith("researcher:") && !view.researchers.includes(id)) view.researchers.push(id);
  return view.nodes[id];
}

function currentIteration(view: RunView): number {
  return view.iterations.at(-1)?.n ?? 1;
}

function apply(view: RunView, e: RunEvent): void {
  const t = Date.parse(e.ts);
  view.events.push(e);
  view.lastAt = t;
  view.startedAt ??= t;
  const d = e.data;
  if (typeof d.cost_usd === "number") view.costUsd = Math.max(view.costUsd, d.cost_usd);

  switch (e.type) {
    case "run.created":
    case "run.started":
      view.status = "running";
      view.request = str(d.request, view.request);
      if (Array.isArray(d.systems)) view.systems = d.systems.map(String);
      break;
    case "step.started": {
      if (!e.node) break;
      const n = node(view, e.node);
      n.status = "active";
      n.label = e.label;
      n.runs += 1;
      view.activeNode = e.node;
      break;
    }
    case "step.completed": {
      if (!e.node) break;
      const n = node(view, e.node);
      n.status = d.ok === false ? "failed" : "done";
      n.label = e.label;
      if (view.activeNode === e.node) view.activeNode = null;
      break;
    }
    case "thought.update":
      if (e.node) node(view, e.node).label = e.label;
      break;
    case "assumption.recorded":
      view.assumptions.push({
        text: str(d.text, e.label),
        reason: str(d.reason),
        overridable: d.overridable !== false,
      });
      break;
    case "decision.recorded":
      view.decisions.push({
        decision: str(d.decision, e.label),
        alternatives: Array.isArray(d.alternatives) ? d.alternatives.map(String) : [],
        rationale: str(d.rationale),
      });
      break;
    case "retrieval.hits":
      view.retrievals.push({
        node: e.node ?? "researcher",
        query: str(d.query, e.label),
        hits: Array.isArray(d.hits) ? (d.hits as Hit[]) : [],
      });
      if (e.node) node(view, e.node);
      break;
    case "clarification.requested":
      view.questions = Array.isArray(d.questions) ? d.questions.map(String) : [e.label];
      break;
    case "iteration.started":
      view.iterations.push({ n: num(d.iteration, view.iterations.length + 1), reason: str(d.reason, e.label) });
      break;
    case "code.delta": {
      const it = num(d.iteration, currentIteration(view));
      if (!view.files[it]) {
        const prior = Object.keys(view.files).map(Number).filter((k) => k < it);
        const base = prior.length ? view.files[Math.max(...prior)] : {};
        view.files[it] = { ...base };
      }
      view.files[it][str(d.path, "main.py")] = str(d.content);
      break;
    }
    case "check.result": {
      const it = num(d.iteration, currentIteration(view));
      view.checks[it] ??= {};
      view.checks[it][str(d.check) as CheckName] = {
        status: (str(d.status, "pass") as CheckStatus),
        summary: str(d.summary, e.label),
        output: typeof d.output === "string" ? d.output : undefined,
      };
      break;
    }
    case "lesson.learned":
      view.lessons.push(str(d.lesson, e.label));
      break;
    case "run.completed":
      view.status = "completed";
      view.endedAt = t;
      view.activeNode = null;
      view.summary = str(d.summary, e.label);
      break;
    case "run.failed":
      view.status = "failed";
      view.endedAt = t;
      view.activeNode = null;
      view.failure = str(d.reason, e.label);
      view.summary = e.label;
      break;
  }
}

/** Iterations that produced files, in order. */
export function codeIterations(view: RunView): number[] {
  return Object.keys(view.files).map(Number).sort((a, b) => a - b);
}

/** Overall verdict for an iteration's checks, if any ran. */
export function iterationVerdict(view: RunView, n: number): CheckStatus | null {
  const results = Object.values(view.checks[n] ?? {});
  if (!results.length) return null;
  return results.some((r) => r?.status === "fail") ? "fail" : "pass";
}
