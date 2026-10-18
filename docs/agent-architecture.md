# Agent Architecture

How the integration agent turns a request into validated code, and what makes it
**intelligent** rather than just a pipeline. For the surrounding system see
[architecture.md](architecture.md). For the reasoning behind each choice see
[decisions.md](decisions.md).

An intelligent agent must:
1. understand the request,
2. notice what it doesn't know,
3. make design decisions and explain them,
4. find the actual cause of its failures,
5. know when to change approach, ask, or stop,
6. get better over time.

Each of these maps to a mechanism in §3.

## 1. Pattern: a fixed workflow with decision points, plus a shared blackboard

Two extremes are rejected:
- **A rigid pipeline:** it can't recover from the unexpected.
- **A free-form supervisor agent that improvises every step:** cost is unbounded, it's
  hard to debug, and eval results are inconsistent.

The design in between:
- **A workflow skeleton defined in code** (a LangGraph `StateGraph`). It fixes which
  stages exist and which transitions are legal.
- **LLM decision points** at the important edges: replan, research again, fix, ask the
  user, stop.
- **A shared blackboard state.** Agents communicate only through typed artifacts on the
  blackboard, never through free-form chat between agents. Every intermediate result can
  be inspected, tested and resumed.

```
                         ┌───────────────────────── BLACKBOARD (typed state) ─────────────────────────┐
                         │ Spec · Assumptions · Plan · InfoNeeds · Evidence · Contract · Tests · Code │
                         │ TestReport · Diagnoses · Lessons · DecisionLog · Budget · BestSoFar        │
                         └────────────────────────────────────────────────────────────────────────────┘
 user ─► ANALYST ──(blocking ambiguity?)──► ask user ◄──────┐
            │ spec                                           │
            ▼                                                │
        ARCHITECT ─► plan + design decisions + InfoNeeds     │
            │   ▲                                            │
            ▼   │ replan (evidence contradicts plan)         │
   RESEARCHERS (parallel, one per provider) ──► Evidence     │
            ▼                                                │
   CONTRACT COMPILER ──(op not in catalog)──► research       │
            ▼                                                │
   TEST AUTHOR ─► acceptance tests (locked)                  │
            ▼                                                │
   CODER ─► workspace                                        │
            ▼                                                │
   VALIDATOR (deterministic gauntlet)                        │
            ▼ fail                         pass ▼            │
   DIAGNOSTICIAN ──► fix │ research │ replan │ ask_user ─────┘ │ stop
            │                              REVIEWER (critic vs. spec + playbook)
            └──► CODER (targeted patch)        ▼ approve
                                          PACKAGER ─► MEMORY CURATOR (learn)
```

## 2. Agents

| Agent | Responsibility | Intelligence lever |
|---|---|---|
| **Analyst** | Request → `IntegrationSpec`: trigger, systems, actions, conditions, non-functional requirements | Sorts ambiguities into blocking (ask) and non-blocking (assume and record) |
| **Architect** | Design decisions, plan, information needs | Weighs alternatives and logs the rationale. Applies the engineering playbook. |
| **Researcher** ×N | Answers each information need with cited evidence. One per provider, run in parallel with LangGraph `Send`. | Stops when every need is answered (or proven unanswerable), not after a fixed top-k. Flags conflicting docs. |
| **Contract Compiler** | Evidence → exact `ApiContract` | Checks every operation against the API catalog in code |
| **Test Author** | Spec → acceptance tests, written **before** the code | Tests come from the requirements, so they're an independent check |
| **Coder** | Writes the project; targeted patches during fixes | Writes only against the contract; reads the lessons list before each attempt |
| **Validator** | Deterministic checks (install, lint, types, grounding, tests, security) | No LLM in the verdict, so the verdict can't be hallucinated |
| **Diagnostician** | Root-cause analysis of failures | Forms hypotheses, classifies the cause, picks the recovery route, writes a lesson |
| **Reviewer** | Checks code against the spec, decisions and playbook once tests pass | Catches "tests pass but the requirement was missed" |
| **Packager** | README, setup steps, decision summary, assumptions, citations | Explains *why*, not just *what* |
| **Memory Curator** | Saves lessons, examples and fixes after each run | Makes later runs better |

Model per role is set in config (see architecture §7). Development runs Haiku 5.5
everywhere. For evals, a single flag moves the reasoning roles to Sonnet 5.5 and the
Coder to Opus 5.5.

## 3. Intelligence mechanisms

### 3.1 Clarification only when the answer changes the design
The Analyst scores each ambiguity on one question: *would the answer change the code?*

- **Blocking:** the run pauses with LangGraph `interrupt()` and asks the user.
  - **Limit: one round, at most 3 questions**, batched in a single message.
- **Non-blocking:** the Analyst makes a sensible assumption and records it in the
  **Assumption Ledger** as `{text, reason, user_overridable}`. The UI shows the ledger and
  the user can override any entry. An override triggers a `followup_edit`.

> Asks: "How do we identify a VIP: a Stripe metadata flag, a HubSpot property, or a spend
> threshold?" Does not ask which Python version to use.

### 3.2 Research driven by a list of questions
The Architect turns the plan into explicit information needs. Each one is
`{question, provider, status: open|answered|unavailable, evidence_ids}`, for example:
- "Which Stripe events count as billing events?"
- "How is a Stripe webhook signature verified?"
- "Which OAuth scope does Slack `chat.postMessage` need?"

Researchers loop with the retrieval tools until every need is `answered` with evidence or
`unavailable`. An unavailable need is reported to the Architect, which may replan.
**The agent tracks what it doesn't know**, which is the main defense against invented
endpoints. It works together with the catalog-checked contract.

### 3.3 Recorded design decisions
Each decision is logged as `{decision, alternatives, rationale, evidence_ids}`, for
example *"Slack `chat.postMessage` instead of an incoming webhook: the alert needs
formatted blocks and the channel must be configurable."* The log is used in three ways:
- The Reviewer checks the code against it.
- It answers user questions like "why did you use X?"
- On a `followup_edit` ("send it to Discord instead"), only the decisions and stages
  affected by the change are recomputed.

### 3.4 Engineering playbook
A curated best-practice corpus is retrieved like any other doc:
- idempotency (dedupe by `event.id`)
- verify signatures before parsing
- return 2xx fast and process asynchronously
- retry with backoff
- respect rate limits
- secrets in environment variables
- timeouts
- structured logging

The Architect applies it unprompted and the Reviewer enforces it. This is what separates
code that merely runs from code a senior engineer would approve.

### 3.5 Test-first, with locked tests
The Test Author writes acceptance tests from the spec before any code exists, for
example "duplicate event is processed once" and "invalid signature → 400". The tests are
then **locked**:
- The Coder may not edit or weaken them.
- A test may change only when the Diagnostician classifies the test itself as wrong
  **and** the Reviewer agrees. Both rulings are recorded in the DecisionLog.

This prevents the classic failure where an LLM makes a red suite green by rewriting the
tests.

### 3.6 Diagnosis, routing and lessons (Reflexion-style)
Each failed validation goes to the Diagnostician. It forms root-cause hypotheses,
classifies the failure, and routes it:

| Root cause | Route |
|---|---|
| Code bug | Coder: targeted patch |
| Missing or wrong knowledge (e.g. unknown payload field) | **Researcher**, with a specific new information need |
| Wrong design (the approach can't meet a requirement) | **Architect**: replan |
| Environment / dependency | Coder: fix requirements |
| Requirement impossible or contradictory | **Ask the user** |

Every diagnosis also writes a one-line **lesson**, for example *"`stripe.Webhook.construct_event`
needs the raw request body bytes, not parsed JSON"*. Lessons stay in the blackboard and go
into every later Coder prompt, so the agent doesn't repeat a mistake within a run.

### 3.7 Self-monitoring: progress, budget, stopping
- **Fingerprints:** each failure is normalized to a fingerprint (check, error class,
  location). If the same fingerprint appears twice, the agent changes strategy: higher
  effort, rewriting the whole file, or a matching fix from fix memory.
- **Best-so-far:** each iteration is scored by checks passed. A patch that causes a
  regression is rolled back.
- **Budget:** the run carries a token, iteration and dollar budget (default: at most 5
  fix iterations). At 80% it switches to finishing up and returns the best version with
  an honest **known-issues report**. It never fails silently.

### 3.8 Learning across runs
After each run the Memory Curator stores:
- the lessons, keyed by provider and topic,
- successful contracts and code as examples, keyed by task embedding,
- fix memory: error fingerprint → patch that resolved it.

The Architect and Coder retrieve these at the start of a run, so the next Stripe task
starts already knowing the pitfalls. This is measured as iterations to green over time.

## 4. Blackboard state

```python
class RunState(TypedDict):
    request: str
    spec: IntegrationSpec              # trigger, systems, actions, conditions, NFRs
    assumptions: list[Assumption]      # {text, reason, user_overridable}
    clarifications: list[Clarification]  # questions asked + answers (max 1 round)
    plan: Plan                         # steps
    decisions: list[Decision]          # {decision, alternatives, rationale, evidence_ids}
    info_needs: list[InfoNeed]         # {question, provider, status, evidence_ids}
    evidence: dict[str, Evidence]      # cited chunks / catalog operations
    contract: ApiContract              # validated against the API catalog
    tests: Workspace                   # locked acceptance tests
    code: Workspace                    # files, versioned per iteration
    reports: list[TestReport]          # one per validation pass
    diagnoses: list[Diagnosis]         # {hypotheses, root_cause, category, route, fingerprint}
    lessons: list[str]                 # this run's Reflexion memory
    best: BestSoFar                    # iteration id + score
    budget: Budget                     # tokens, iterations, dollars: limits and spent
```

Everything on the blackboard is a Pydantic model. Each node declares which fields it
reads and writes, which keeps nodes testable on their own with fixture states.

## 5. Walkthrough

*"Build a webhook that listens to billing events and alerts Slack for VIP customers."*

1. **Analyst:** one blocking ambiguity, the VIP definition, so it asks. It records these
   assumptions:
   - "billing events" = `invoice.paid`, `invoice.payment_failed`,
     `customer.subscription.deleted`
   - a single alert channel
2. **Architect:**
   - Chooses `chat.postMessage` (decision logged).
   - Applies the playbook: idempotency, signature verification, async processing.
   - Lists 7 information needs.
3. **Researchers** (Stripe and Slack in parallel): answer all 7 with citations, and note
   that signature verification requires the raw request body.
4. **Contract → Tests → Code:** 9 locked acceptance tests, including duplicate-event and
   invalid-signature cases.
5. **Validator:** pyright flags an SDK method that doesn't exist. The **Diagnostician**
   classifies it as missing knowledge and sends a targeted re-research query. The fix
   lands and the checks pass.
6. **Reviewer:** the Slack call has no timeout, a playbook violation. One more patch, then
   approved.
7. **Packager:** delivers code, setup steps, decisions, assumptions and citations.
8. **Memory Curator:** stores 2 lessons.

Throughout the run the UI shows events such as *Asked about VIP definition*,
*Assumption recorded*, *Replanned: research gap on signature verification* and
*Caught non-existent SDK method, re-researching*.

## 6. Guardrails

| Guardrail | Value |
|---|---|
| Clarification | 1 round, at most 3 questions |
| Fix iterations | at most 5 (configurable) |
| Repeat-failure trigger | same fingerprint twice → strategy switch |
| Budget wind-down | at 80% of any budget |
| Test edits | only with Diagnostician + Reviewer rulings |
| External access | Researchers: allowlisted doc domains. Generated code: mock servers only. |

## 7. Measuring the intelligence

| Metric | What it shows |
|---|---|
| Clarification precision | It asked only when the answer changed the design |
| Assumption accuracy | Its defaults match what a human engineer would choose |
| Information-need coverage | Share of needs answered with valid evidence |
| Root-cause accuracy | Diagnostician's category vs. labeled failures |
| Playbook violations at delivery | Code quality beyond "tests pass" |
| Iterations to green, before vs. after memory | Learning across runs |
| Hallucinated operations with vs. without catalog grounding | Value of the RAG design |
