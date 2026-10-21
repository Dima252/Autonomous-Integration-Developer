# System Architecture

This document describes the whole system: request routing, retrieval, code validation,
streaming, storage and models. The agent design is in
[agent-architecture.md](agent-architecture.md). The reasoning behind each major choice is
in [decisions.md](decisions.md).

## Product requirements

| # | Requirement | How it is met |
|---|---|---|
| R1 | Small talk is answered instantly, without starting expensive processes | Two-tier router (§2). Most chit-chat never reaches an LLM, and the rest goes to the cheapest model. |
| R2 | Uses the exact, correct API docs and never invents endpoints | Structured API catalog + hybrid RAG + a grounding contract (§3) |
| R3 | Generated code is checked and fixed automatically until it is robust | Deterministic validation + diagnose/fix loop (§4, and the agent doc) |
| R4 | The user sees every internal step in real time | Typed run events → Redis Streams → resumable SSE (§5) |

## Design principles

1. **A fixed workflow with LLM decision points.** Code defines the stages and the allowed
   transitions. LLMs make the judgment calls at specific decision points. The system is
   never a free-form agent conversation.
2. **Deterministic checks before LLM checks.** Compilers, type checkers, tests and schema
   matching run before any LLM critic.
3. **Each request pays only for the tier it needs.** "Hi" costs about $0 and returns in
   well under a second.
4. **Every claim is measurable.** An eval harness and ablation studies back each one.

## 1. Overview

```
┌──────────────── Next.js chat UI ────────────────┐
│ chat · live run timeline · code/diff · citations│
└──────┬──────────────────────────▲───────────────┘
  POST /chat                SSE /runs/{id}/events (resumable)
       ▼                          │
┌──────────────── FastAPI gateway ┴───────────────┐
│  Router: Tier 0 rules+embeddings → Tier 1 LLM   │
└───┬──────────────┬──────────────────┬───────────┘
 chit-chat     docs Q&A          integration task
 direct reply  light RAG (1 call) enqueue run ──► Redis (queue + Streams event log)
                                                    ▼
                      ┌──────── Worker: LangGraph run ────────┐
                      │ analyst → architect → researchers →   │
                      │ contract → tests → coder → validator  │
                      │ ⟲ diagnostician → … → reviewer →      │
                      │ packager → memory curator             │
                      └───┬──────────────┬────────────────────┘
                          ▼              ▼
          Postgres 17 + pgvector     Sandbox (Docker, no network)
          docs index, API catalog,   + Prism mock servers built
          checkpoints, chats, memory   from provider OpenAPI specs
```

Components:

| Component | Responsibility |
|---|---|
| **Web** (Next.js) | Chat, live run timeline, code viewer with diffs between iterations, citations, assumption ledger |
| **API gateway** (FastAPI) | Auth, routing, the cheap paths (chit-chat, docs Q&A), run creation, SSE |
| **Worker** (Arq + LangGraph) | Executes integration runs. A run outlives any single HTTP request. |
| **Postgres + pgvector** | Docs index, API catalog, LangGraph checkpoints, conversations, long-term memory |
| **Redis** | Job queue, per-run event streams, rate limits |
| **Sandbox** | Runs untrusted generated code with no network access, alongside Prism mock servers |

## 2. Smart routing (R1)

| Tier | Mechanism | Latency | Cost | Handles |
|---|---|---|---|---|
| 0 | Rules (greetings, thanks) + nearest-neighbor match against labeled example messages | < 30 ms | $0 | Obvious small talk |
| 1 | Small-model classifier with structured output: `intent`, `confidence`, `systems[]` | ~300 ms | ~$0.0001 | Everything else |

Intents and their routes:

| Intent | Route |
|---|---|
| `chitchat` | Direct streamed reply from the small model |
| `docs_question` | A single RAG call. No graph, no worker. |
| `integration_task` | Enqueue an agent run |
| `followup_edit` | **Resume the existing run from its checkpoint** with a modified spec. Only the affected stages re-run. |
| `clarify` | Ask the user. If confidence is low, a question is cheaper than a wrong heavy run. |

## 3. Retrieval (R2)

API docs are semi-structured, and plain vector search over text chunks is how agents end
up inventing endpoints. Retrieval therefore has two surfaces.

### 3.1 Structured API catalog: exact lookups
Each provider's OpenAPI spec (Stripe, Slack, GitHub, HubSpot) is parsed into tables:
- `api_operations`: method, path, op_id, params, request/response schemas, scopes, plus
  a path regex for matching concrete requests back to an operation
- `api_webhook_events`: event name, payload schema
- `api_schemas`: named schemas. Operations refer to them by `$ref` and callers expand
  references to the depth they need (ADR 010).

The catalog is the **source of truth**. Generated code may only call operations that
exist here (see the contract in the agent doc).

| Provider | Spec | Operations | Webhook events |
|---|---|---|---|
| Stripe | OpenAPI 3.0 | 612 | 268, from the `enabled_events` enum, typed via `x-resourceId` |
| GitHub | OpenAPI 3.0 + `x-webhooks` | 1,242 | 275, full payload schemas |
| Slack | Swagger 2.0 (archived 2021) | 174 | none in the spec; covered by the docs index |
| HubSpot | 8 curated files out of ~700 | 82 | 48 names; payloads only in prose docs |

Sources are pinned to commit SHAs and ingestion is keyed by content hash, so re-running
`python -m aidev.catalog ingest` is a no-op until a pin changes. Provider quirks live in
small *profiles* (`catalog/profiles.py`), so the parser stays provider-agnostic.

### 3.2 Hybrid semantic index: fuzzy discovery
- **Documents:**
  - One per operation, webhook event and schema.
  - Guide pages split along their headings into chunks of ~500–800 tokens, each with a
    breadcrumb prefix (`Slack › Messaging › chat.postMessage › Arguments`).
- **Contextual retrieval:** at ingest time a small model writes 1–2 sentences that
  situate each chunk. This runs through the Batch API, with the full page prompt-cached.
- **Search:**
  - Postgres full-text search (BM25-style) + pgvector HNSW, merged with Reciprocal Rank
    Fusion, then the **Voyage reranker** narrows the top 50 to the top 8.
  - Metadata filters: `provider`, `doc_type`, `api_version`.
- **Embeddings:** Voyage AI, behind `Embedder` / `Reranker` interfaces so a local model
  can be swapped in.

### 3.3 Engineering playbook corpus
A curated set of integration best practices is indexed alongside the vendor docs:
- idempotency
- signature verification
- returning 2xx fast and processing asynchronously
- retries with backoff
- respecting rate limits
- secrets in environment variables
- timeouts
- structured logging

The Architect applies it unprompted and the Reviewer enforces it.

### 3.4 Agent retrieval tools
- `search_docs(query, provider, doc_type)`
- `get_operation(op_id)`
- `get_webhook_event(type)`
- `get_schema(name)`
- `fetch_live_doc(url)`: domain-allowlisted. Fetched pages are indexed on the fly.

### 3.5 Freshness and safety
- Ingestion is versioned by content hash, with scheduled re-ingest.
- Retrieved text is treated as **data, never as instructions**, which defends against
  prompt injection planted in third-party docs.

## 4. Validation pipeline (R3)

The checks run in the sandbox, cheapest and most decisive first:

| # | Check | Catches |
|---|---|---|
| 1 | `uv pip install` the generated requirements | Missing or invented packages |
| 2 | `ruff` + `pyright` with the real provider SDKs installed | Syntax and type errors, **SDK methods that don't exist** |
| 3 | Grounding check: AST-extract every HTTP call and match it to the catalog | Invented REST endpoints and params |
| 4 | `pytest` against Prism mock servers, plus **signed** webhook payload replays | Behavioral bugs, broken signature verification |
| 5 | `semgrep` / `bandit`, secret scan | Security issues, hardcoded secrets |
| 6 | LLM Reviewer (only once 1–5 pass) | Unmet requirements, playbook violations |

The diagnose/fix loop that consumes these results is described in the agent doc.

## 5. Streaming (R4)

**Event shape:** one typed envelope for every event:

```json
{ "run_id": "…", "seq": 42, "type": "iteration.started", "node": "coder",
  "label": "Caught missing dependency `stripe`, rewriting…", "data": { } }
```

**Event types:**
- `run.started` / `run.completed` / `run.failed`
- `step.started` / `step.completed`
- `clarification.requested`
- `assumption.recorded`
- `retrieval.hits` (with citations)
- `decision.recorded`
- `thought.update` (the model's progress notes between tool calls)
- `code.delta`
- `check.result`
- `iteration.started`
- `lesson.learned`

**Pipeline:**
1. Graph nodes emit events through LangGraph's custom stream writer.
2. The worker appends them to a Redis Stream per run (`XADD`).
3. A FastAPI SSE endpoint serves them and honors `Last-Event-ID`, so a refresh replays
   and resumes.

Finished runs are saved to Postgres for a "replay this run" view. Step labels come from
templates, so they cost nothing. Only diagnosis text is model-written.

## 6. State and memory

| Scope | Storage | Contents |
|---|---|---|
| Run state | LangGraph `PostgresSaver` | Typed blackboard per thread. Resumable and inspectable, and you can rewind to earlier steps. |
| Conversation | Postgres `messages` | Full history plus a rolling summary for long threads |
| User preferences | Postgres | Language, framework, style |
| Solution memory | pgvector | Successful runs (task embedding → contract + code), used as examples |
| Lessons / fix memory | pgvector | Lessons per provider; error fingerprint → successful patch |
| Events | Redis Streams | Replayable run event log |

## 7. Models and cost

All LLM calls go through a provider-agnostic `LLMClient`. Each role selects its model in
config, so switching is one line.

| Role | Development | Final evals / demo |
|---|---|---|
| Router, chit-chat, ingest enrichment, packager, memory curator | `claude-haiku-5-5` | `claude-haiku-5-5` |
| Analyst, architect, researcher, diagnostician, reviewer | `claude-haiku-5-5` | `claude-sonnet-5-5` (config flag) |
| Coder | `claude-haiku-5-5` | `claude-opus-5-5` |

Prices per million tokens (input / output):

| Model | Price |
|---|---|
| Haiku 5.5 | $0.10 / $0.50 |
| Sonnet 5.5 | $2 / $10 |
| Opus 5.5 | $4 / $20 |

Expected cost per heavy run:
- **Development (Haiku everywhere):** ~$0.04
- **Final configuration:** ~$1 before caching

Expected project total: **under $15**. Voyage embeddings and reranking fall within the
free token allowance.

**Cost levers:**
- **Prompt caching:** in the fix loop, the system prompt, contract and current files form a
  stable cached prefix, and each iteration only appends the new error report.
- **Batch API:** used for ingestion.
- **Per-role effort settings.**

**Claude API notes:**
- Set `effort` explicitly. Opus 5.5 defaults to `medium`.
- Forced `tool_choice` is rejected on Opus 5.5. Use `strict` tools with `auto`, or
  structured outputs.

## 8. Security

- **Generated code** runs only in the sandbox: no network except the mock servers, no host
  mounts, CPU/memory/time limits.
- **No real credentials** ever enter the sandbox. Tests use mock servers and fake signing
  secrets.
- **Retrieved docs and user input** are data. Tool outputs are never executed as
  instructions.
- **`fetch_live_doc`** is restricted to an allowlist of official documentation domains.

## 9. Observability and evaluation

- **Tracing:** Langfuse (self-hosted) + OpenTelemetry. Every LLM call, tool call and node
  transition is traced with its cost.
- **Golden set:** ~30 integration tasks across 4 providers, run in CI.
- **System metrics:**
  - task success rate
  - iterations to green
  - hallucinated-endpoint rate **with and without RAG** (ablation)
  - retrieval recall@8
  - router accuracy and the share of traffic served without the heavy path
  - chit-chat time-to-first-token
  - cost per task
- **Agent metrics:** see the agent doc, §7.

## 10. Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, uv |
| Orchestration | LangGraph + Postgres checkpointer |
| LLMs | Anthropic SDK behind `LLMClient` |
| Retrieval | Postgres 17 + pgvector, Postgres full-text search, Voyage embeddings + reranker |
| Queue / events | Redis 7, Arq, Redis Streams |
| Sandbox | Docker (`--network none`), Stoplight Prism |
| Frontend | Next.js (App Router), TypeScript, Tailwind, shadcn/ui, Zustand, Monaco |
| Observability | Langfuse, OpenTelemetry |
| CI | GitHub Actions |

## 11. Scope of v1

- **Providers:** Stripe, Slack, GitHub, HubSpot.
- **Generated code:** Python (FastAPI services, webhook handlers, API clients).
- **Sandbox:** local Docker. A hosted demo uses replay mode or a self-managed VM.
