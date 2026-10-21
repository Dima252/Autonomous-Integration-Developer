# Architecture Decision Log

Short records of the major design decisions. Each entry gives the context, the decision,
and what it costs us.

| # | Decision | Status |
|---|---|---|
| 001 | Fixed workflow with LLM decision points, not a free-form supervisor agent | Accepted |
| 002 | Python + FastAPI backend, LangGraph orchestration | Accepted |
| 003 | Postgres + pgvector as the single primary database; Redis for queue and events | Accepted |
| 004 | Claude models at minimal cost, behind a provider-agnostic LLM layer | Accepted |
| 005 | Hosted Voyage embeddings and reranker behind swappable interfaces | Accepted |
| 006 | Structured API catalog + contract for grounding, not vector search alone | Accepted |
| 007 | Deterministic validation first; locked test-first acceptance tests | Accepted |
| 008 | Clarification policy: at most one round of 3 blocking questions | Accepted |
| 009 | Local Docker sandbox with Prism mock servers | Accepted |
| 010 | API catalog: named schema refs, provider profiles, pinned sources | Accepted |

---

## 001: Fixed workflow with LLM decision points

**Context.** Agent systems range from rigid pipelines to fully autonomous supervisor
agents that choose every step. Rigid pipelines can't recover from surprises. Free-form
agents have unbounded cost, are hard to debug, and score inconsistently on evals.

**Decision.**
- Code defines the stages and the legal transitions (LangGraph `StateGraph`).
- LLMs make judgment calls only at defined decision points: replan, research again, fix,
  ask the user, stop.
- Agents communicate only through a typed blackboard state.

**Consequences.**
- Bounded cost, reproducible runs, and nodes that can be unit-tested.
- Every intermediate artifact can be inspected and resumed.
- The agent can only take paths that exist in the graph. New behaviors need new edges.

## 002: Python + FastAPI, LangGraph orchestration

**Context.** The backend runs LLM orchestration, retrieval, and sandboxed Python
validation (ruff, pyright, pytest).

**Decision.**
- Python 3.12 with FastAPI, Pydantic v2 and uv.
- LangGraph for orchestration: typed state, conditional edges, `Send` fan-out,
  `interrupt()` for clarification, and a Postgres checkpointer.
- LLMs are called through the official Anthropic SDK inside nodes, not through LangChain
  model wrappers, so new API features (effort, caching, strict tools, structured outputs)
  are available immediately.

**Consequences.**
- The strongest ecosystem for both the AI side and the validation tooling.
- Python 3.12 rather than 3.14, for library compatibility.
- The frontend is a separate TypeScript app.

## 003: Postgres + pgvector, plus Redis

**Context.** We need vector search, full-text search, relational catalog data, agent
checkpoints, conversations and memory.

**Decision.**
- **Postgres 17 + pgvector** holds all of it.
- **Redis** handles the job queue (Arq) and per-run event streams (Redis Streams).

**Consequences.**
- One database to run, back up and query. Hybrid search and catalog joins happen in SQL.
- A dedicated vector database might scale further, which this project doesn't need.

## 004: Claude at minimal cost, provider-agnostic LLM layer

**Context.** It's a personal project with no budget. Fully local LLMs (a 16 GB GPU is
available) are noticeably weaker at multi-file code and tool calling. Free API tiers are
rate-limited and change often.

**Decision.**
- All LLM calls go through an `LLMClient` interface, with the model chosen per role in
  config.
- **Development:** `claude-haiku-5-5` for every role (~$0.04 per heavy run).
- **Final evals and demo:** one flag moves the reasoning roles to `claude-sonnet-5-5` and
  the Coder to `claude-opus-5-5`.
- A local-model adapter is an optional later addition, for comparison in the evals.

**Consequences.**
- Expected total spend under $15.
- Claude-specific features (prompt caching, progress notes) are optional extras in the
  Claude adapter, not something the core depends on.

## 005: Hosted Voyage embeddings and reranker

**Context.** Running embeddings and a reranker locally costs $0 but needs PyTorch/CUDA,
large downloads and a GPU. That complicates CI and any hosted demo.

**Decision.**
- Use Voyage AI embeddings and its reranker, behind `Embedder` / `Reranker` interfaces.
- The project's expected volume (an index of ~5–15M tokens, plus a few thousand reranked
  searches) fits within Voyage's free token allowance.

**Consequences.**
- Simple setup that works in CI and hosting.
- A network dependency, and free-tier rate limits until a payment method is added.
- Switching to a local model (`bge-m3`, `bge-reranker-v2-m3`) is a new adapter and a
  config change.

## 006: API catalog + contract for grounding

**Context.** Vector search over doc chunks returns text that is *similar*, which is how
agents end up inventing endpoints and parameters.

**Decision.**
- Provider OpenAPI specs are parsed into an exact API catalog (operations, webhook events,
  schemas).
- A hybrid semantic index is used for discovery.
- Research ends in an `ApiContract` in which every operation is checked against the
  catalog in code. Generated code is checked against it again (AST grounding check), and
  pyright runs against the real SDKs.

**Consequences.**
- Invented endpoints are caught by code, not by an LLM's judgment.
- Requires a reliable OpenAPI spec for each supported provider.

## 007: Deterministic validation first; locked acceptance tests

**Context.** An LLM critic can be fooled, and an LLM fixer can "pass" by weakening tests.

**Decision.**
- The validator runs, in order: dependency install, ruff, pyright, grounding check,
  pytest against mock servers, and security scans.
- An LLM Reviewer runs only after all of these pass.
- Acceptance tests are written from the spec before the code, then locked. They may
  change only when both the Diagnostician and the Reviewer rule the test itself wrong;
  both rulings are recorded.

**Consequences.**
- The verdict can be trusted, and test results can't be gamed.
- The Test Author must be good, because a wrong test costs fix iterations until the
  diagnosis catches it.

## 008: Clarification policy

**Context.** An agent that never asks builds the wrong thing. An agent that always asks
is annoying.

**Decision.**
- The Analyst classifies each ambiguity by whether the answer would change the code.
- Blocking ambiguities are asked in **one round of at most 3 questions**.
- Everything else is assumed and recorded in an Assumption Ledger the user can override.

**Consequences.**
- Fast runs with transparent defaults.
- Clarification precision and assumption accuracy become eval metrics.

## 009: Local Docker sandbox with Prism mock servers

**Context.** Generated code is untrusted and must be executed to be validated. Real API
calls would need credentials and have side effects.

**Decision.**
- Run validation in Docker containers with no network access, resource limits and no
  host mounts.
- Prism serves mock APIs generated from the same OpenAPI specs as the catalog.
- Webhook tests replay sample payloads signed with test secrets.

**Consequences.**
- Safe, free, and repeatable.
- Mock servers can't reproduce every real-world API behavior.
- A public hosted demo needs either a VM that can run Docker or a replay mode.

## 010: API catalog: named schema refs, provider profiles, pinned sources

**Context.** The four v1 providers publish specs very differently. Stripe has no webhooks
section (event types exist only as an enum). GitHub uses the `x-webhooks` extension, and
its spec keys (`check-run`) differ from the event names it delivers (`check_run`).
Slack's only spec is Swagger 2.0, archived in 2021, and lists legacy OAuth scopes.
HubSpot publishes ~700 files. The specs are also large and recursive: Stripe's
`customer` and `subscription` schemas refer to each other.

**Decision.**
- One parser for OpenAPI 3.x and Swagger 2.0. Named schemas stay as `$ref`s
  (normalized to `#/components/schemas/<name>`) and are stored once in `api_schemas`.
  `expand_refs(depth=n)` inlines them on demand. Every other `$ref` (parameters, request
  bodies, responses) is inlined, so an operation is complete on its own.
- Provider quirks go in small **profiles**: Stripe events from the enum, typed through
  `x-resourceId`; GitHub event naming; Slack scopes from each method's token
  description; HubSpot event names from the subscription enum.
- Payloads the spec doesn't describe are left empty rather than hand-written. The docs
  index covers them.
- Sources are pinned to commit SHAs. Ingestion skips a document whose content hash
  is unchanged and replaces a changed one atomically.
- HubSpot is limited to the 8 files CRM integrations need (objects, properties,
  associations, owners, webhooks).

**Consequences.**
- Full dereferencing would have blown up on recursive specs. Keeping refs stores each
  schema once (the whole catalog is ~10 MB in Postgres) and lets agents choose how much
  detail they pull.
- A grounding lookup is one indexed SQL query: an invented path or method matches
  nothing.
- Slack methods added after 2021 aren't in the catalog. Agents must find them in the
  docs index and the contract compiler will flag them as unverified.
- Adding a provider means a source entry and, at most, a small profile.
