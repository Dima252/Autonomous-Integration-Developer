// Mirrors the backend contract: aidev.chat.service (chat events) and
// aidev.events.bus (run events). docs/ui-events.md documents the `data` payloads.

export type Intent =
  | "chitchat"
  | "docs_question"
  | "integration_task"
  | "followup_edit"
  | "clarify";

export interface RouteInfo {
  intent: Intent;
  tier: number;
  confidence: number;
  systems: string[];
  reason: string;
  latency_ms: number;
  degraded: boolean;
}

export interface Citation {
  n: number;
  title: string;
  url: string | null;
  breadcrumb: string;
  provider: string;
  doc_type: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  route?: RouteInfo;
  citations?: Citation[];
  runId?: string;
  costUsd?: number;
  latencyMs?: number;
  error?: string;
  streaming?: boolean;
}

export type RunEventType =
  | "run.created"
  | "run.started"
  | "run.completed"
  | "run.failed"
  | "step.started"
  | "step.completed"
  | "clarification.requested"
  | "assumption.recorded"
  | "retrieval.hits"
  | "decision.recorded"
  | "thought.update"
  | "code.delta"
  | "check.result"
  | "iteration.started"
  | "lesson.learned";

export interface RunEvent {
  run_id: string;
  seq: number;
  ts: string; // ISO 8601
  type: RunEventType;
  label: string;
  node: string | null;
  data: Record<string, unknown>;
}

export type CheckName =
  | "install"
  | "ruff"
  | "pyright"
  | "grounding"
  | "pytest"
  | "security"
  | "review";

export type CheckStatus = "pass" | "fail" | "skip";
