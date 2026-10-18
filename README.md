# Autonomous Integration Developer

A chat-based virtual engineer that writes **and validates** API integrations.
Ask for something like *"Build a webhook that listens to Stripe billing events and
alerts Slack for VIP customers"* and it researches the real API docs, writes the code,
tests it in a sandbox against mock APIs, and fixes its own mistakes until the checks
pass. You watch every step stream live.

> **Status:** Phase 0 (foundation) complete. See the [roadmap](#roadmap).

## How it works

```
Chat UI ──► Router ──► chit-chat ─────────► fast, cheap reply (no agents)
               │
               └────► integration task ──► LangGraph run (streamed to the UI)
                        analyst (clarify / assume) → architect (decisions + info needs)
                        → researchers (RAG, per provider, parallel)
                        → API contract (every operation checked against the catalog)
                        → locked acceptance tests → coder → validator
                        ⟲ diagnostician (fix | re-research | replan | ask user)
                        → reviewer → packager → memory curator (learns)
```

Full design docs:
- [System architecture](docs/architecture.md): routing, RAG, validation, streaming, models, cost
- [Agent architecture](docs/agent-architecture.md): agents, blackboard state, the mechanisms that make it intelligent
- [Decision log](docs/decisions.md): the major design decisions and their trade-offs

- **Cost-aware routing.** Rules and embedding matching handle small talk without an LLM.
  A small model classifies the rest. Only real integration tasks start the agent graph.
- **Grounded RAG.** Provider OpenAPI specs are parsed into an exact *API catalog*
  (endpoints, webhook events, schemas). Hybrid search (BM25 + pgvector + reranker) covers
  the docs. Generated code may only use operations that exist in the catalog.
- **Self-healing loop.** Deterministic checks run first: dependency install, ruff, pyright
  against the real SDKs, an endpoint grounding check, and pytest against Prism mock servers
  with signed webhook replays. An LLM reviewer runs last. A diagnostician finds the root
  cause of each failure and routes it to a code fix, more research, a replan, or a
  question for the user. Acceptance tests are written before the code and locked, so they
  can't be weakened to make a run pass. The loop keeps the best version so far and stops
  on budget or when it stops making progress.
- **Intelligent agent design.** It asks only the clarifying questions that change the
  design and records every other assumption. Research works through an explicit list of
  questions until each one is answered or shown to be unanswerable. Design decisions are
  logged with their rationale. A curated engineering playbook covers idempotency,
  signatures, retries and timeouts. Lessons from past runs carry over, so it improves over
  time.
- **Transparent execution.** Typed run events go through Redis Streams to a resumable SSE
  stream, so the UI shows each step as it happens.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic, uv |
| Orchestration | LangGraph (Postgres checkpointer) |
| LLMs | Claude Haiku 5.5 / Sonnet 5.5 / Opus 5.5 behind a provider-agnostic layer |
| Retrieval | Postgres + pgvector, full-text search, Voyage AI embeddings + reranker |
| Events / queue | Redis 7 (Streams, Arq) |
| Sandbox | Docker (no network) + Prism mock servers from OpenAPI specs |
| Frontend | Next.js, TypeScript, Tailwind, shadcn/ui, Monaco |
| Observability | Langfuse, OpenTelemetry |

## Quickstart

Prerequisites: Docker Desktop, [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
docker compose up -d                 # Postgres (pgvector) + Redis

cd backend
uv sync
uv run uvicorn aidev.api.main:app --reload
# http://localhost:8000/health   http://localhost:8000/ready   http://localhost:8000/docs
```

Checks (the same ones CI runs):

```bash
cd backend
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run pytest                        # unit tests
uv run pytest -m integration         # needs docker compose up
```

## Roadmap

- [x] **Phase 0: Foundation.** Repo layout, Docker services, health/readiness, CI
- [ ] **Phase 1: RAG.** OpenAPI → API catalog, doc chunking, engineering playbook, hybrid search, retrieval eval
- [ ] **Phase 2: Chat.** Router, chat path, SSE event bus, minimal UI
- [ ] **Phase 3: Agent graph.** analyst → architect → researchers → contract → tests → coder
- [ ] **Phase 4: Self-healing.** Sandbox, validation pipeline, diagnose/fix loop
- [ ] **Phase 5: UI.** Run timeline, iteration diff viewer, citations
- [ ] **Phase 6: Quality.** Memory, tracing, eval harness with ablations, deploy

## Repository layout

```
docs/               Architecture, agent design, decision log
backend/            Python backend (package: aidev)
  src/aidev/api/    FastAPI gateway
  src/aidev/infra/  Postgres / Redis clients
infra/postgres/     DB init scripts (pgvector)
.github/workflows/  CI
```
