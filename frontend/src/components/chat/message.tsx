"use client";

import { motion, useReducedMotion } from "motion/react";
import { ArrowUpRight, Coins, Route, TriangleAlert, Workflow, Zap } from "lucide-react";
import { PROVIDER_NAMES } from "@/lib/agents";
import { INTENT_LABELS, cx, ms, usd } from "@/lib/format";
import type { ChatMessage, Citation, RouteInfo } from "@/lib/types";
import { Badge } from "../ui";
import { Markdown } from "./markdown";

export function Message({
  message,
  onOpenRun,
}: {
  message: ChatMessage;
  onOpenRun?: (runId: string) => void;
}) {
  const reduce = useReducedMotion();
  const isUser = message.role === "user";
  return (
    <motion.li
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className={cx("flex flex-col gap-1.5", isUser ? "items-end" : "items-start")}
    >
      {isUser ? (
        <div className="max-w-[88%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-accent-soft px-3.5 py-2 text-sm text-text">
          {message.content}
        </div>
      ) : (
        <div className="w-full max-w-full">
          {message.content ? (
            // A block cursor typed into the text keeps it on the last line while streaming.
            <Markdown text={message.streaming ? `${message.content} ▍` : message.content} />
          ) : (
            <TypingDots />
          )}
        </div>
      )}

      {message.error && (
        <p className="flex items-center gap-1.5 text-xs text-fail">
          <TriangleAlert size={14} aria-hidden="true" /> {message.error}
        </p>
      )}
      {message.citations && message.citations.length > 0 && <Citations citations={message.citations} />}
      {message.runId && onOpenRun && (
        <button
          onClick={() => onOpenRun(message.runId!)}
          className="flex items-center gap-2 rounded-lg border border-border bg-raised px-3 py-2 text-xs text-muted transition-colors hover:border-border-strong hover:text-text"
        >
          <Workflow size={14} className="text-accent" aria-hidden="true" />
          Agent run in the workspace
          <ArrowUpRight size={14} aria-hidden="true" />
        </button>
      )}
      {!isUser && (message.route || message.costUsd !== undefined) && (
        <Meta route={message.route} costUsd={message.costUsd} latencyMs={message.latencyMs} />
      )}
      {isUser && message.route && <Meta route={message.route} />}
    </motion.li>
  );
}

function Meta({ route, costUsd, latencyMs }: { route?: RouteInfo; costUsd?: number; latencyMs?: number }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {route && <RouteBadge route={route} />}
      {latencyMs !== undefined && (
        <Badge title="Time to complete the reply">
          <Zap size={11} aria-hidden="true" /> {ms(latencyMs)}
        </Badge>
      )}
      {costUsd !== undefined && (
        <Badge title="What this reply cost, including routing">
          <Coins size={11} aria-hidden="true" /> {usd(costUsd)}
        </Badge>
      )}
    </div>
  );
}

export function RouteBadge({ route }: { route: RouteInfo }) {
  const systems = route.systems.map((s) => PROVIDER_NAMES[s] ?? s).join(", ");
  const tier = route.tier === 0 ? "tier 0 · no model" : "tier 1 · classifier";
  return (
    <Badge
      tone={route.degraded ? "warn" : route.tier === 0 ? "ok" : "accent"}
      title={`${route.reason} · confidence ${route.confidence.toFixed(2)} · routed in ${ms(route.latency_ms)}`}
    >
      <Route size={11} aria-hidden="true" />
      {INTENT_LABELS[route.intent]}
      <span className="opacity-70">· {tier}</span>
      {systems && <span className="opacity-70">· {systems}</span>}
    </Badge>
  );
}

function Citations({ citations }: { citations: Citation[] }) {
  return (
    <ol className="flex flex-wrap gap-1.5" aria-label="Sources">
      {citations.map((c) => (
        <li key={c.n}>
          <a
            href={c.url ?? undefined}
            target="_blank"
            rel="noreferrer noopener"
            title={c.breadcrumb}
            className={cx(
              "flex max-w-64 items-center gap-1.5 rounded-md border border-border bg-raised px-2 py-1 text-[11px] text-muted transition-colors",
              c.url ? "hover:border-border-strong hover:text-text" : "pointer-events-none",
            )}
          >
            <span className="font-mono text-accent">[{c.n}]</span>
            <span className="truncate">{c.title}</span>
            <span className="shrink-0 text-faint">{PROVIDER_NAMES[c.provider] ?? c.provider}</span>
          </a>
        </li>
      ))}
    </ol>
  );
}

function TypingDots() {
  return (
    <span className="flex h-6 items-center gap-1" aria-label="Thinking">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-faint"
          style={{ animationDelay: `${i * 120}ms` }}
        />
      ))}
    </span>
  );
}
