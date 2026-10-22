# Autonomous Integration Developer

A chat-based virtual engineer that writes **and validates** API integrations.
Ask for something like *"Build a webhook that listens to Stripe billing events and
alerts Slack for VIP customers"* and it researches the real API docs, writes the code,
tests it in a sandbox against mock APIs, and fixes its own mistakes until the checks
pass. You watch every step stream live.

> **Status:** Phase 2 in progress. Done: the LLM layer with spend caps, the two-tier
> router, the streaming chat API (small talk, cited docs answers, run events), the
> mission-control UI and the on-demand live-mode server. Next: the static public demo on
> AWS. See the [roadmap](#roadmap).

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
- [ ] **Phase 2: Chat + deploy.** Router, chat path, SSE event bus, the UI, and a public
  demo with continuous deployment from day one.
  - [x] LLM layer with hard monthly spend caps; two-tier router and its eval (ADR 012, 013)
  - [x] Chat API: template and model replies, cited docs answers, run events over
    resumable SSE, rate limits (ADR 014)
  - [x] Mission-control UI: chat, live agent graph, timeline, code diffs per iteration,
    checks, sources, spec and decisions; a scripted sample run (ADR 015)
  - [x] Live-mode server: Docker images, a Lightsail stack with HTTPS, CD over SSH
    (ADR 016)
  - [ ] Static public demo: the UI as static files on S3 + CloudFront, chat that says
    when live mode is offline, Playwright tests in CI (ADR 017)
- [ ] **Phase 3: Sandbox + validation.** Docker sandbox, Prism mocks, and the deterministic
  checks (install, lint, types, grounding, tests, security), proven on hand-written good
  and bad code before any agent depends on them
- [ ] **Phase 4: Agent graph + fix loop.** A thin slice first (analyst → researcher → coder →
  validator → diagnostician on one Stripe → Slack task), then the architect, contract
  compiler, test author, reviewer and packager
- [ ] **Phase 5: Recorded real runs.** Export finished runs to the public demo in place of
  the scripted sample; assumption overrides, run history
- [ ] **Phase 6: Quality.** Memory, tracing, eval harness with ablations

## Deployment

The demo is for people evaluating the project, who have a minute, not ten. So the public
demo and live mode are deployed separately, both on AWS (ADR 016, 017):

```
Visitors ──► CloudFront ──► S3: static UI + recorded real runs        always on, ~$0/month

Live mode ──► Lightsail (on demand) ──► Caddy ─┬─► UI
                                              └─► FastAPI + worker ─┬─► Postgres + pgvector
                                                                    ├─► Redis
                                                                    └─► Docker sandbox + Prism mocks
```

- **Public demo:** the UI as static files on S3 behind CloudFront. It replays **recorded
  real runs**: tasks run locally, with their event logs exported and played back through
  the same view live mode uses. The scripted sample run stays, and is labeled as such.
  No backend and no API keys, so it costs nothing and nothing can break.
- **Live mode, on demand:** the full stack on one Lightsail instance with 2 GB of RAM
  (~$12/month, billed by the hour). It's created for an interview or a live test and
  deleted afterwards. A VM is needed because platform hosts can't run the Docker sandbox
  that executes generated code. Setup and teardown are in
  [infra/deploy/](infra/deploy/README.md).
- **Real use:** the full stack runs locally with `docker compose`.
- **Continuous deployment:** once CI passes on `main`, GitHub Actions publishes the
  static UI to S3 through an IAM role it assumes with OIDC, so no AWS keys are stored in
  GitHub. While the live server exists, Actions also pushes the images to GitHub
  Container Registry and redeploys the server over SSH.
- **Spend limits:** a monthly cap in the Anthropic console, hard per-month caps in the
  app (ADR 013), per-IP rate limits, and an AWS Budgets alert.
- **Showcase:** a 1–2 minute demo video at the top of this README, the demo link, and
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
