import { SseParser } from "./sse";
import type { Citation, RouteInfo, RunEvent, RunEventType } from "./types";

// Same origin behind the reverse proxy in production; the API directly in development.
export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "/api").replace(/\/$/, "");

export type ChatStreamEvent =
  | { type: "conversation"; data: { conversation_id: string; message_id: number } }
  | { type: "route"; data: RouteInfo }
  | { type: "citations"; data: { query: string; hits: Citation[]; degraded: string[] } }
  | { type: "run"; data: { run_id: string; events_url: string } }
  | { type: "delta"; data: { text: string } }
  | { type: "error"; data: { message: string } }
  | { type: "done"; data: { message_id: number; cost_usd: number; latency_ms: number } };

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** POST /chat and call `onEvent` for each streamed event until the reply is done. */
export async function streamChat(
  message: string,
  conversationId: string | null,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message, conversation_id: conversationId }),
      signal,
    });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new ApiError(0, "Can't reach the API. Is the backend running?");
  }
  if (!resp.ok || !resp.body) throw new ApiError(resp.status, await errorDetail(resp));

  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const msg of parser.push(value)) {
      onEvent({ type: msg.event, data: JSON.parse(msg.data) } as ChatStreamEvent);
    }
  }
}

const RUN_EVENT_TYPES: RunEventType[] = [
  "run.created",
  "run.started",
  "run.completed",
  "run.failed",
  "step.started",
  "step.completed",
  "clarification.requested",
  "assumption.recorded",
  "retrieval.hits",
  "decision.recorded",
  "thought.update",
  "code.delta",
  "check.result",
  "iteration.started",
  "lesson.learned",
];

/** Subscribes to a run's events. EventSource reconnects with Last-Event-ID by itself;
 * the subscription closes after the run's terminal event. Returns an unsubscribe. */
export function subscribeRun(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onError?: () => void,
): () => void {
  const source = new EventSource(`${API_BASE}/runs/${runId}/events`);
  const handle = (msg: MessageEvent<string>) => {
    const event = JSON.parse(msg.data) as RunEvent;
    onEvent(event);
    if (event.type === "run.completed" || event.type === "run.failed") source.close();
  };
  for (const type of RUN_EVENT_TYPES) source.addEventListener(type, handle);
  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) onError?.();
  };
  return () => source.close();
}

async function errorDetail(resp: Response): Promise<string> {
  try {
    const body = await resp.json();
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // not JSON
  }
  if (resp.status === 429) return "Too many messages. Try again in a minute.";
  if (resp.status === 503) return "Chat isn't configured on this server.";
  return `Request failed (${resp.status})`;
}
