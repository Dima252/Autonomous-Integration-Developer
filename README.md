# Autonomous Integration Developer

A chat-based virtual engineer that writes **and validates** API integrations.
Ask for something like *"Build a webhook that listens to Stripe billing events and
alerts Slack for VIP customers"* and it researches the real API docs, writes the code,
tests it in a sandbox against mock APIs, and fixes its own mistakes until the checks
pass. You watch every step stream live.

> **Status:** Phase 2 in progress. Done: the LLM layer with spend caps, the two-tier
> router, the streaming chat API (small talk, cited docs answers, run events) and the
> mission-control UI. Next: the first deploy. See the [roadmap](#roadmap).

![Mission control: chat on the left; on the right the agent graph mid-run, with the
validator failed and the diagnostician working on the fix](docs/images/mission-control.png)

<sub>The UI playing its scripted sample run. The agents that produce real runs are Phase
3; the UI already renders their [event contract](docs/ui-events.md).</sub>

## How it works

```
Chat UI ──► Router ──► chit-chat ─────────► fast, cheap reply (no agents)
               ├────► docs question ─────► one search + a cited answer (no agents)
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

- **Cost-aware routing.** Rules and embedding matching answer small talk without waiting
  for an LLM (~0.3 s instead of ~1.3 s). A small model classifies the rest: 109 of 109
  correct on the golden set, typos and prompt-injection attempts included. Only real
  integration tasks start the agent graph. Every paid call is metered against hard
  monthly caps.
- **Grounded RAG.** Provider OpenAPI specs are parsed into an exact *API catalog*
  (endpoints, webhook events, schemas). Vector search plus a reranker covers the docs
  (Hit@8 0.98 on a 66-query golden set), with BM25 in SQL as the fallback. Generated
  code may only use operations that exist in the catalog.
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
  stream. The UI draws the agent graph live, with the code at every iteration, the checks,
  the sources, and the decisions and assumptions behind them.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic, uv |
| Orchestration | LangGraph (Postgres checkpointer) |
| LLMs | Claude Haiku 5.5 / Sonnet 5.5 / Opus 5.5 behind a provider-agnostic layer |
| Retrieval | Postgres + pgvector, full-text search, Voyage AI embeddings + reranker |
| Events / queue | Redis 7 (Streams, Arq) |
| Sandbox | Docker (no network) + Prism mock servers from OpenAPI specs |
| Frontend | Next.js, TypeScript, Tailwind, React Flow, Monaco, Motion |
| Observability | Langfuse, OpenTelemetry |

## Quickstart

Prerequisites: Docker Desktop, [uv](https://docs.astral.sh/uv/), Node.js 24 (for the UI).

```bash
cp .env.example .env
docker compose up -d                 # Postgres (pgvector) + Redis

cd backend
uv sync
uv run alembic upgrade head          # create / update the database schema
uv run python -m aidev.catalog ingest  # load the API catalog (~10 s, downloads ~25 MB once)
uv run python -m aidev.rag index     # build the docs index (full-text search works now)
uv run python -m aidev.rag embed     # embed it (needs VOYAGE_API_KEY; resumable)
uv run python -m aidev.rag search "verify a stripe webhook signature" --provider stripe
uv run python -m aidev.router route "alert slack when a stripe dispute opens"  # needs ANTHROPIC_API_KEY
uv run python -m aidev.llm usage     # month-to-date spend against the monthly caps
uv run uvicorn aidev.api.main:app --reload
# http://localhost:8000/health   http://localhost:8000/ready   http://localhost:8000/docs
```

The web UI (separate terminal):

```bash
cd frontend
npm install
npm run dev                          # http://localhost:3000 (API on :8000)
```

Chat over the API (Server-Sent Events):

```bash
curl -N localhost:8000/chat -H 'content-type: application/json' \
  -d '{"message": "how do I verify a stripe webhook signature?"}'
# event: route      data: {"intent": "docs_question", "tier": 1, ...}
# event: citations  data: {"hits": [{"n": 1, "title": ..., "url": ...}, ...]}
# event: delta      data: {"text": "Pass the raw request body, the Stripe-Signature ..."}
# event: done       data: {"cost_usd": 0.0006, "latency_ms": 4800, ...}
```

Checks (the same ones CI runs):

```bash
cd backend
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run pytest                        # unit tests
uv run pytest -m integration         # needs docker compose up

cd ../frontend
npm run lint && npm run typecheck && npm test && npm run build
```

## Roadmap

- [x] **Phase 0: Foundation.** Repo layout, Docker services, health/readiness, CI
- [x] **Phase 1: RAG.** OpenAPI → API catalog, doc chunking, engineering playbook, search, retrieval eval
- [ ] **Phase 2: Chat + first deploy.** Router, chat path, SSE event bus, the UI.
  Deployed to a VM with HTTPS and continuous deployment from day one.
  - [x] LLM layer with hard monthly spend caps; two-tier router and its eval (ADR 012, 013)
  - [x] Chat API: template and model replies, cited docs answers, run events over
    resumable SSE, rate limits (ADR 014)
  - [x] Mission-control UI: chat, live agent graph, timeline, code diffs per iteration,
    checks, sources, spec and decisions; a scripted sample run (ADR 015)
  - [ ] VM deploy with HTTPS and continuous deployment
- [ ] **Phase 3: Agent graph.** analyst → architect → researchers → contract → tests → coder
- [ ] **Phase 4: Self-healing.** Sandbox, validation pipeline, diagnose/fix loop
- [ ] **Phase 5: UI.** Assumption overrides, replay of recorded real runs, run history
  (the run workspace itself shipped in Phase 2)
- [ ] **Phase 6: Quality.** Memory, tracing, eval harness with ablations, public demo hardening

## Deployment

The whole stack runs on one small VM with `docker compose`, the same way it runs locally.
Platform hosts (Vercel, Render, Railway) can't run the Docker sandbox that executes
generated code, so a VM is the simplest host that runs everything.

```
Internet ─► Caddy (automatic HTTPS) ─┬─► Next.js UI
                                     └─► FastAPI + worker ─┬─► Postgres + pgvector
                                                           ├─► Redis
                                                           └─► Docker sandbox + Prism mocks
```

- **Host:** an AWS Lightsail instance with 2 GB of RAM (~$12/month), with Caddy
  providing automatic HTTPS (ADR 016).
- **Continuous deployment:** once CI passes on `main`, GitHub Actions builds the images,
  pushes them to GitHub Container Registry, and redeploys the VM over SSH. The setup
  steps and day-to-day commands are in [infra/deploy/](infra/deploy/README.md).
- **Demo safety:**
  - **Replay mode by default:** visitors watch real recorded runs stream step by step.
    No API cost, and nothing can break.
  - **Live mode behind a passcode,** shared with reviewers.
  - **Spend limits:** a monthly cap in the Anthropic console, a per-run token and dollar
    budget, and per-IP rate limits.
- **Showcase:** a 1–2 minute demo video at the top of this README, the live link, and
  the eval results.

## Repository layout

```
docs/                 Architecture, agent design, decision log, UI event contract
frontend/             Next.js mission-control UI (see frontend/README.md)
backend/              Python backend (package: aidev)
  src/aidev/api/      FastAPI gateway: health, chat and run-event endpoints (SSE)
  src/aidev/chat/     Chat path: conversations, replies, docs answers, run creation
  src/aidev/events/   Typed run events on Redis Streams
  src/aidev/catalog/  API catalog: OpenAPI parser, provider profiles, store, ingest CLI
  src/aidev/rag/      Docs index: corpus, chunking, embeddings, BM25 + vector search, eval
  src/aidev/llm/      LLM client interface, Claude adapter, pricing, spend meter and caps
  src/aidev/router/   Two-tier router: rules, nearest neighbour, classifier, eval
  src/aidev/infra/    Postgres / Redis clients
  migrations/         Alembic database migrations
corpus/playbook/      Engineering playbook (indexed alongside provider docs)
corpus/router/        Labeled example messages for the router's tier 0
corpus/eval/          Golden sets for evaluation
infra/postgres/       DB init scripts (pgvector)
infra/deploy/         Production compose stack, Caddyfile, VM scripts, runbook
.github/workflows/    CI, and the deploy to the VM
```
