// The agent graph's nodes (docs/agent-architecture.md §1-2) and their layout:
// three rows, "Plan", "Build & heal" and "Ship". Researchers fan out per provider.

export type Stage = "plan" | "build" | "ship";

export interface AgentMeta {
  id: string;
  name: string;
  role: string;
  stage: Stage;
}

export const AGENTS: AgentMeta[] = [
  { id: "analyst", name: "Analyst", role: "Turns the request into a spec", stage: "plan" },
  { id: "architect", name: "Architect", role: "Decides the design", stage: "plan" },
  { id: "researcher", name: "Researcher", role: "Answers questions from the docs", stage: "plan" },
  { id: "contract", name: "Contract", role: "Checks every call against the catalog", stage: "plan" },
  { id: "test_author", name: "Test author", role: "Writes locked acceptance tests", stage: "build" },
  { id: "coder", name: "Coder", role: "Writes the code", stage: "build" },
  { id: "validator", name: "Validator", role: "Runs the deterministic checks", stage: "build" },
  { id: "diagnostician", name: "Diagnostician", role: "Finds the root cause", stage: "build" },
  { id: "reviewer", name: "Reviewer", role: "Checks code against the spec", stage: "ship" },
  { id: "packager", name: "Packager", role: "Writes the README and summary", stage: "ship" },
  { id: "memory_curator", name: "Memory", role: "Saves lessons for next time", stage: "ship" },
];

const BY_ID = new Map(AGENTS.map((a) => [a.id, a]));

/** Metadata for a node id; "researcher:stripe" resolves to the researcher. */
export function agentFor(nodeId: string): AgentMeta & { provider?: string } {
  const [base, provider] = nodeId.split(":");
  const meta = BY_ID.get(base) ?? {
    id: base,
    name: base,
    role: "",
    stage: "build" as Stage,
  };
  return provider ? { ...meta, provider } : meta;
}

export const PROVIDER_NAMES: Record<string, string> = {
  stripe: "Stripe",
  slack: "Slack",
  github: "GitHub",
  hubspot: "HubSpot",
  general: "Playbook",
  other: "Other",
};

export function displayName(nodeId: string): string {
  const meta = agentFor(nodeId);
  return meta.provider ? `${meta.name} · ${PROVIDER_NAMES[meta.provider] ?? meta.provider}` : meta.name;
}

// Layout, in graph coordinates.
export const NODE_W = 204;
export const NODE_H = 64;
const COL = 248;
const ROW_Y: Record<Stage, number> = { plan: 40, build: 250, ship: 430 };

export function layout(researchers: string[]): Record<string, { x: number; y: number }> {
  const pos: Record<string, { x: number; y: number }> = {
    analyst: { x: 0, y: ROW_Y.plan },
    architect: { x: COL, y: ROW_Y.plan },
    contract: { x: COL * 3, y: ROW_Y.plan },
    test_author: { x: 0, y: ROW_Y.build },
    coder: { x: COL, y: ROW_Y.build },
    validator: { x: COL * 2, y: ROW_Y.build },
    diagnostician: { x: COL * 3, y: ROW_Y.build },
    reviewer: { x: COL, y: ROW_Y.ship },
    packager: { x: COL * 2, y: ROW_Y.ship },
    memory_curator: { x: COL * 3, y: ROW_Y.ship },
  };
  const list = researchers.length ? researchers : ["researcher"];
  const span = (list.length - 1) * (NODE_H + 16);
  list.forEach((id, i) => {
    pos[id] = { x: COL * 2, y: ROW_Y.plan - span / 2 + i * (NODE_H + 16) };
  });
  return pos;
}

export const CHECKS = [
  { id: "install", name: "Install", detail: "uv pip install the requirements" },
  { id: "ruff", name: "Lint", detail: "ruff" },
  { id: "pyright", name: "Types", detail: "pyright with the real SDKs" },
  { id: "grounding", name: "Grounding", detail: "every HTTP call exists in the catalog" },
  { id: "pytest", name: "Tests", detail: "pytest against Prism mocks + signed webhooks" },
  { id: "security", name: "Security", detail: "semgrep, bandit, secret scan" },
  { id: "review", name: "Review", detail: "LLM reviewer against spec and playbook" },
] as const;
