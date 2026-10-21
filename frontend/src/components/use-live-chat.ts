"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, streamChat, subscribeRun } from "@/lib/api";
import type { ChatMessage, RunEvent } from "@/lib/types";

export interface LiveChat {
  messages: ChatMessage[];
  sending: boolean;
  runEvents: RunEvent[];
  runId: string | null;
  send: (text: string) => Promise<void>;
  reset: () => void;
}

const newId = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Math.random()));

export function useLiveChat(): LiveChat {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [runEvents, setRunEvents] = useState<RunEvent[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const conversation = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const unsubscribe = useRef<(() => void) | null>(null);

  useEffect(
    () => () => {
      abort.current?.abort();
      unsubscribe.current?.();
    },
    [],
  );

  const watchRun = useCallback((id: string) => {
    unsubscribe.current?.();
    setRunId(id);
    setRunEvents([]);
    unsubscribe.current = subscribeRun(id, (event) => setRunEvents((events) => [...events, event]));
  }, []);

  const send = useCallback(
    async (text: string) => {
      const userId = newId();
      const replyId = newId();
      const patch = (id: string, change: (m: ChatMessage) => ChatMessage) =>
        setMessages((ms) => ms.map((m) => (m.id === id ? change(m) : m)));
      setMessages((ms) => [
        ...ms,
        { id: userId, role: "user", content: text },
        { id: replyId, role: "assistant", content: "", streaming: true },
      ]);
      setSending(true);
      abort.current = new AbortController();
      try {
        await streamChat(
          text,
          conversation.current,
          (event) => {
            switch (event.type) {
              case "conversation":
                conversation.current = event.data.conversation_id;
                break;
              case "route":
                patch(replyId, (m) => ({ ...m, route: event.data }));
                break;
              case "citations":
                patch(replyId, (m) => ({ ...m, citations: event.data.hits }));
                break;
              case "run":
                patch(replyId, (m) => ({ ...m, runId: event.data.run_id }));
                watchRun(event.data.run_id);
                break;
              case "delta":
                patch(replyId, (m) => ({ ...m, content: m.content + event.data.text }));
                break;
              case "error":
                patch(replyId, (m) => ({ ...m, error: event.data.message }));
                break;
              case "done":
                patch(replyId, (m) => ({
                  ...m,
                  streaming: false,
                  costUsd: event.data.cost_usd,
                  latencyMs: event.data.latency_ms,
                }));
                break;
            }
          },
          abort.current.signal,
        );
      } catch (err) {
        if (!abort.current?.signal.aborted) {
          const message = err instanceof ApiError ? err.message : "Something went wrong.";
          patch(replyId, (m) => ({ ...m, error: message }));
        }
      } finally {
        patch(replyId, (m) => ({ ...m, streaming: false }));
        setSending(false);
      }
    },
    [watchRun],
  );

  const reset = useCallback(() => {
    abort.current?.abort();
    unsubscribe.current?.();
    conversation.current = null;
    setMessages([]);
    setRunEvents([]);
    setRunId(null);
    setSending(false);
  }, []);

  return { messages, sending, runEvents, runId, send, reset };
}
