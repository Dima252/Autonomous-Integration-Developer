// Plays a scripted run against a clock. Everything visible derives from `elapsed`,
// so scrubbing is just setting it.

import type { SampleRun } from "@/data/sample-run";
import type { ChatMessage, RunEvent } from "./types";

const BASE_TS = Date.parse("2026-10-21T12:00:00Z");
const TYPE_CHARS_PER_MS = 0.12; // assistant text "streams" at ~120 chars/s

export function scriptedEvents(sample: SampleRun): RunEvent[] {
  return sample.events.map((e, i) => ({
    run_id: sample.id,
    seq: i + 1,
    ts: new Date(BASE_TS + e.t).toISOString(),
    type: e.type,
    label: e.label,
    node: e.node ?? null,
    data: e.data ?? {},
  }));
}

/** Until the last event, and the last message has finished typing out. */
export function duration(sample: SampleRun): number {
  const typed = sample.messages.map((m) =>
    m.role === "assistant" ? m.t + Math.ceil(m.content.length / TYPE_CHARS_PER_MS) : m.t,
  );
  return Math.max(sample.events.at(-1)?.t ?? 0, ...typed) + 1500;
}

/** How many scripted events have happened by `elapsed`. */
export function eventsAt(sample: SampleRun, elapsed: number): number {
  let n = 0;
  while (n < sample.events.length && sample.events[n].t <= elapsed) n++;
  return n;
}

/** The chat as it looks at `elapsed`, with the newest assistant message typed out. */
export function messagesAt(sample: SampleRun, elapsed: number): ChatMessage[] {
  return sample.messages
    .filter((m) => m.t <= elapsed)
    .map((m, i) => {
      const shown =
        m.role === "assistant"
          ? Math.min(m.content.length, Math.floor((elapsed - m.t) * TYPE_CHARS_PER_MS) + 1)
          : m.content.length;
      return {
        id: `${sample.id}-${i}`,
        role: m.role,
        content: m.content.slice(0, shown),
        streaming: shown < m.content.length,
        route: m.route,
        costUsd: shown === m.content.length ? m.costUsd : undefined,
        latencyMs: shown === m.content.length ? m.latencyMs : undefined,
        runId: m.run ? sample.id : undefined,
      };
    });
}
