"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ArrowUp, Play, Radio, Sparkles } from "lucide-react";
import type { ChatMessage } from "@/lib/types";
import { Button, Kbd } from "../ui";
import { Message } from "./message";

export const EXAMPLES = [
  "Build a webhook that alerts Slack when a Stripe payment fails for a VIP customer",
  "How do I verify a Stripe webhook signature?",
  "What OAuth scope does Slack chat.postMessage need?",
  "What does a GitHub pull_request webhook payload contain?",
];

export function ChatPanel({
  mode,
  messages,
  sending,
  onSend,
  onOpenRun,
  onGoLive,
  onPlaySample,
}: {
  mode: "sample" | "live";
  messages: ChatMessage[];
  sending: boolean;
  onSend: (text: string) => void;
  onOpenRun: (runId: string) => void;
  onGoLive: () => void;
  onPlaySample: () => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const last = messages.at(-1);
  const lastLength = last?.content.length ?? 0;

  // Follow the conversation while it grows, unless the reader scrolled up.
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
    if (nearBottom) el.scrollTo({ top: el.scrollHeight });
  }, [messages.length, lastLength]);

  return (
    <section aria-label="Chat" className="flex h-full min-h-0 flex-col">
      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-5">
        {messages.length === 0 ? (
          <Welcome mode={mode} onSend={onSend} onPlaySample={onPlaySample} />
        ) : (
          <ol className="flex flex-col gap-5">
            {messages.map((m) => (
              <Message key={m.id} message={m} onOpenRun={onOpenRun} />
            ))}
          </ol>
        )}
      </div>
      {mode === "live" ? (
        <Composer sending={sending} onSend={onSend} />
      ) : (
        <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-3">
          <p className="text-xs text-muted">This is a scripted sample run. Chat with the real backend in Live mode.</p>
          <Button variant="primary" size="sm" onClick={onGoLive} className="shrink-0 whitespace-nowrap">
            <Radio size={14} aria-hidden="true" /> Try it live
          </Button>
        </div>
      )}
    </section>
  );
}

function Welcome({
  mode,
  onSend,
  onPlaySample,
}: {
  mode: "sample" | "live";
  onSend: (text: string) => void;
  onPlaySample: () => void;
}) {
  return (
    <div className="flex h-full flex-col justify-center gap-5">
      <div>
        <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-accent">
          <Sparkles size={14} aria-hidden="true" /> Autonomous Integration Developer
        </p>
        <h2 className="mt-2 text-xl font-semibold text-text">Describe an integration. Watch it get built.</h2>
        <p className="mt-2 text-sm text-muted">
          Small talk and docs questions take a cheap path. Integration tasks start an agent run that
          researches the real API docs, writes the code, tests it and fixes its own mistakes.
        </p>
      </div>
      {mode === "live" ? (
        <ul className="flex flex-col gap-2" aria-label="Example requests">
          {EXAMPLES.map((e) => (
            <li key={e}>
              <button
                onClick={() => onSend(e)}
                className="w-full rounded-lg border border-border bg-raised px-3 py-2.5 text-left text-sm text-muted transition-colors hover:border-border-strong hover:text-text"
              >
                {e}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <Button variant="primary" onClick={onPlaySample} className="self-start">
          <Play size={14} aria-hidden="true" /> Play the sample run
        </Button>
      )}
    </div>
  );
}

function Composer({ sending, onSend }: { sending: boolean; onSend: (text: string) => void }) {
  const [text, setText] = useState("");
  const submit = () => {
    const value = text.trim();
    if (!value || sending) return;
    onSend(value);
    setText("");
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="border-t border-border p-3"
    >
      <label htmlFor="composer" className="sr-only">
        Message
      </label>
      <div className="flex items-end gap-2 rounded-xl border border-border bg-raised p-2 focus-within:border-accent">
        <textarea
          id="composer"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={Math.min(6, Math.max(1, text.split("\n").length))}
          maxLength={4000}
          placeholder="Ask about an API, or describe an integration…"
          className="max-h-40 min-h-9 flex-1 resize-none bg-transparent px-1.5 py-1.5 text-sm text-text outline-none placeholder:text-faint focus-visible:outline-none"
        />
        <button
          type="submit"
          disabled={!text.trim() || sending}
          aria-label="Send"
          className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-on-accent transition-colors hover:bg-accent-strong disabled:opacity-40"
        >
          <ArrowUp size={16} aria-hidden="true" />
        </button>
      </div>
      <p className="mt-1.5 px-1 text-[11px] text-faint">
        <Kbd>Enter</Kbd> to send · <Kbd>Shift</Kbd>+<Kbd>Enter</Kbd> for a new line · <Kbd>Ctrl</Kbd>+<Kbd>K</Kbd> for commands
      </p>
    </form>
  );
}
