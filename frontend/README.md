# Frontend

The mission-control UI: chat on the left, a live workspace for the agent run on the
right (agent graph, timeline, code with per-iteration diffs, checks, sources, and the
spec and decisions ledger). See [ADR 015](../docs/decisions.md) for the design and
[docs/ui-events.md](../docs/ui-events.md) for the event contract it renders.

Next.js (App Router), TypeScript, Tailwind CSS, React Flow, Monaco, Motion.

```bash
npm install
npm run dev          # http://localhost:3000, talks to the API on :8000 (.env.development)
npm test             # vitest: SSE parser, run reducer, replay timing
npm run lint && npm run typecheck && npm run build
```

- **Sample run** mode plays a scripted run (`src/data/sample-run.ts`) through the same
  reducer the live mode uses. It needs no backend and costs nothing, which makes it the
  default for visitors.
- **Live** mode streams `POST /chat` and subscribes to `GET /runs/{id}/events`.
- In production the UI and API share one origin, with the API under `/api`
  (`NEXT_PUBLIC_API_URL` overrides it).

Layout:

```
src/app/                  layout (fonts, theme script), page
src/components/           mission control, chat, workspace tabs, command palette
src/lib/types.ts          the backend's chat and run event shapes
src/lib/run-state.ts      folds run events into what the workspace shows (pure)
src/lib/replay.ts         the sample run's clock
src/lib/api.ts, sse.ts    streaming chat over fetch, run events over EventSource
src/data/sample-run.ts    the scripted three-iteration run
```
