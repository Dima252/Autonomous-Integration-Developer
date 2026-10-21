"use client";

import { useEffect, useRef, useState } from "react";
import {
  BookOpen,
  ChevronRight,
  CircleHelp,
  FileCode,
  GraduationCap,
  Lightbulb,
  ListChecks,
  MessageSquare,
  PenLine,
  Play,
  RotateCcw,
  Scale,
  Flag,
  type LucideIcon,
} from "lucide-react";
import { displayName } from "@/lib/agents";
import { clock, cx } from "@/lib/format";
import type { RunView } from "@/lib/run-state";
import type { RunEvent, RunEventType } from "@/lib/types";
import { StatusIcon } from "../ui";

const ICONS: Partial<Record<RunEventType, LucideIcon>> = {
  "run.created": Play,
  "run.started": Play,
  "run.completed": Flag,
  "run.failed": Flag,
  "thought.update": MessageSquare,
  "assumption.recorded": Lightbulb,
  "decision.recorded": Scale,
  "retrieval.hits": BookOpen,
  "clarification.requested": CircleHelp,
  "code.delta": FileCode,
  "check.result": ListChecks,
  "lesson.learned": GraduationCap,
  "iteration.started": RotateCcw,
};

function detail(e: RunEvent): string | null {
  const d = e.data;
  switch (e.type) {
    case "assumption.recorded":
      return typeof d.reason === "string" ? d.reason : null;
    case "decision.recorded":
      return typeof d.rationale === "string" ? d.rationale : null;
    case "check.result":
      return typeof d.output === "string" ? d.output : typeof d.summary === "string" ? d.summary : null;
    case "retrieval.hits":
      return Array.isArray(d.hits)
        ? (d.hits as { breadcrumb: string }[]).map((h) => h.breadcrumb).join("\n")
        : null;
    case "clarification.requested":
      return Array.isArray(d.questions) ? d.questions.join("\n") : null;
    case "lesson.learned":
      return typeof d.lesson === "string" ? d.lesson : null;
    default:
      return null;
  }
}

export function Timeline({ run }: { run: RunView }) {
  const listRef = useRef<HTMLDivElement>(null);
  const start = run.startedAt ?? 0;
  const count = run.events.length;

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 200) el.scrollTo({ top: el.scrollHeight });
  }, [count]);

  if (!count) return <p className="p-6 text-sm text-faint">No events yet.</p>;
  return (
    <div ref={listRef} className="h-full overflow-y-auto px-4 py-3">
      <ol className="relative">
        {run.events.map((e) => (
          <Row key={e.seq} event={e} offset={Date.parse(e.ts) - start} />
        ))}
      </ol>
    </div>
  );
}

function Row({ event: e, offset }: { event: RunEvent; offset: number }) {
  const [open, setOpen] = useState(false);
  const extra = detail(e);
  if (e.type === "iteration.started") {
    return (
      <li className="my-3 flex items-center gap-3">
        <span className="h-px flex-1 bg-border" />
        <span className="flex items-center gap-1.5 rounded-full border border-warn/40 bg-warn-soft px-3 py-1 text-xs font-medium text-warn">
          <RotateCcw size={12} aria-hidden="true" /> {e.label}
        </span>
        <span className="h-px flex-1 bg-border" />
      </li>
    );
  }
  const Icon = ICONS[e.type];
  const status =
    e.type === "step.started"
      ? "active"
      : e.type === "step.completed"
        ? e.data.ok === false
          ? "failed"
          : "done"
        : e.type === "check.result"
          ? (e.data.status as "pass" | "fail" | "skip")
          : e.type === "run.failed"
            ? "failed"
            : e.type === "run.completed"
              ? "done"
              : null;
  return (
    <li className="group">
      <button
        onClick={() => extra && setOpen(!open)}
        aria-expanded={extra ? open : undefined}
        className={cx(
          "flex w-full items-start gap-3 rounded-md px-2 py-1.5 text-left",
          extra ? "hover:bg-hover" : "cursor-default",
        )}
      >
        <span className="mt-0.5 w-10 shrink-0 font-mono text-[11px] text-faint">{clock(offset)}</span>
        <span className="mt-0.5 shrink-0 text-muted">
          {status ? <StatusIcon status={status} size={14} /> : Icon ? <Icon size={14} aria-hidden="true" /> : <PenLine size={14} aria-hidden="true" />}
        </span>
        <span className="min-w-0 flex-1 text-[13px] text-text">
          {e.node && <span className="mr-1.5 text-muted">{displayName(e.node)}</span>}
          <span className={cx(e.type === "thought.update" && "italic text-muted")}>{e.label}</span>
        </span>
        {extra && (
          <ChevronRight
            size={14}
            aria-hidden="true"
            className={cx("mt-0.5 shrink-0 text-faint transition-transform", open && "rotate-90")}
          />
        )}
      </button>
      {open && extra && (
        <pre className="mb-2 ml-[4.75rem] mr-2 overflow-x-auto whitespace-pre-wrap rounded-md border border-border bg-bg p-2.5 font-mono text-[11px] leading-relaxed text-muted">
          {extra}
        </pre>
      )}
    </li>
  );
}
