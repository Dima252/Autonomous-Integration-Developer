# UI event contract

The web UI renders an integration run purely from its event stream
(`GET /runs/{id}/events`, [architecture §5](architecture.md#5-streaming-r4)). This page
pins down what each event's `data` carries, so the Phase 3 agent graph emits exactly what
the workspace can show. The scripted sample run (`frontend/src/data/sample-run.ts`) follows
this contract, and `frontend/src/lib/run-state.ts` is the reducer that reads it.

Every event has the envelope from `aidev.events.bus`:

```json
{ "run_id": "…", "seq": 42, "ts": "2026-10-21T12:00:31Z", "type": "check.result",
  "node": "validator", "label": "pytest failed", "data": { } }
```

- `label` is one human-readable line. The timeline and the agent graph show it as is.
- `node` names the graph node that emitted it: `analyst`, `architect`,
  `researcher:<provider>` (one per provider, e.g. `researcher:stripe`), `contract`,
  `test_author`, `coder`, `validator`, `diagnostician`, `reviewer`, `packager`,
  `memory_curator`.
- Any event may carry `data.cost_usd`: the run's cumulative model spend so far. The UI
  shows the highest value it has seen.

## Payloads

| Type | `data` | Shown as |
|---|---|---|
| `run.created`, `run.started` | `request: str`, `systems: str[]` | Run header |
| `step.started` | – | Node turns active (pulsing); its subtitle becomes `label` |
| `step.completed` | `ok?: bool` (false marks the node failed) | Node done (✓) or failed (✗) |
| `thought.update` | – | Node subtitle; italic line in the timeline |
| `clarification.requested` | `questions: str[]` | Spec & decisions › Asked |
| `assumption.recorded` | `text: str`, `reason: str`, `overridable: bool` | Spec & decisions › Assumed |
| `decision.recorded` | `decision: str`, `alternatives: str[]`, `rationale: str` | Spec & decisions › Decided |
| `retrieval.hits` | `query: str`, `hits: [{title, url, breadcrumb, provider, doc_type}]` | Sources |
| `iteration.started` | `iteration: int`, `reason: str` | Separator in the timeline; the self-healing loop's counter |
| `code.delta` | `iteration: int`, `path: str`, `content: str` (the whole file) | Code: file tree, editor, diff against the previous iteration |
| `check.result` | `iteration: int`, `check: install\|ruff\|pyright\|grounding\|pytest\|security\|review`, `status: pass\|fail\|skip`, `summary: str`, `output?: str` | Checks matrix and output panel |
| `lesson.learned` | `lesson: str`, `provider: str` | Spec & decisions › Learned |
| `run.completed` | `summary: str`, `iterations: int`, `cost_usd: float`, `duration_s: float` | Status "Completed" |
| `run.failed` | `reason: str` (`not_implemented` while Phase 3 is pending) | Status "Stopped" and an explanation |

## Rules the reducer relies on

- **Code is sent as whole files, not patches.** The UI keeps one snapshot per iteration;
  files an iteration didn't touch are carried over from the one before. The diff view
  compares consecutive snapshots.
- **Acceptance tests live under `tests/`** and are shown as locked: they're written before
  the code and may not be weakened by a fix (ADR 007).
- **Events for one run arrive in order** (`seq` ascending, one writer per run).
- **Unknown types and fields are ignored**, so the backend can add events before the UI
  shows them.
