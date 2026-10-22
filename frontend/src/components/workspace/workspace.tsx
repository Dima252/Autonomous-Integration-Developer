"use client";

import type { ReactNode } from "react";
import { BookOpen, Code, Coins, History, ListChecks, Network, Play, RotateCcw, ScrollText, Timer } from "lucide-react";
import type { SampleRun } from "@/data/sample-run";
import { PROVIDER_NAMES } from "@/lib/agents";
import { clock, cx, usd } from "@/lib/format";
import { iterationVerdict, type RunView } from "@/lib/run-state";
import { Badge, Button } from "../ui";
import { AgentGraph } from "./agent-graph";
import { ChecksView } from "./checks-view";
import { CodeView } from "./code-view";
import { LedgerView } from "./ledger-view";
import { ReplayBar, type ReplayControls } from "./replay-bar";
import { SourcesView } from "./sources-view";
import { Timeline } from "./timeline";

export type Tab = "graph" | "timeline" | "code" | "checks" | "sources" | "ledger";

export function Workspace({
  run,
  tab,
  onTab,
  theme,
  mode,
  offline,
  sample,
  replay,
  onPlaySample,
}: {
  run: RunView;
  tab: Tab;
  onTab: (tab: Tab) => void;
  theme: "dark" | "light";
  mode: "sample" | "live";
  offline: boolean;
  sample: SampleRun;
  replay: ReplayControls;
  onPlaySample: () => void;
}) {
  const latest = run.iterations.at(-1)?.n;
  const failing = latest !== undefined && iterationVerdict(run, latest) === "fail";
  const fileCount = Object.keys(run.files[Math.max(0, ...Object.keys(run.files).map(Number))] ?? {}).length;
  const tabs: { id: Tab; label: string; icon: ReactNode; badge?: ReactNode }[] = [
    { id: "graph", label: "Agents", icon: <Network size={14} /> },
    { id: "timeline", label: "Timeline", icon: <History size={14} />, badge: run.events.length || undefined },
    { id: "code", label: "Code", icon: <Code size={14} />, badge: fileCount || undefined },
    {
      id: "checks",
      label: "Checks",
      icon: <ListChecks size={14} />,
      badge: latest !== undefined && Object.keys(run.checks).length ? (
        <span className={cx("h-2 w-2 rounded-full", failing ? "bg-fail" : "bg-ok")} aria-label={failing ? "failing" : "passing"} />
      ) : undefined,
    },
    { id: "sources", label: "Sources", icon: <BookOpen size={14} />, badge: run.retrievals.length || undefined },
    {
      id: "ledger",
      label: "Spec & decisions",
      icon: <ScrollText size={14} />,
      badge: run.assumptions.length + run.decisions.length || undefined,
    },
  ];

  return (
    <section aria-label="Run workspace" className="flex h-full min-h-0 flex-col">
      <RunHeader run={run} mode={mode} />
      <div role="tablist" aria-label="Workspace views" className="flex gap-1 overflow-x-auto border-b border-border px-2">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls="workspace-panel"
            onClick={() => onTab(t.id)}
            className={cx(
              "relative flex h-10 shrink-0 items-center gap-1.5 px-3 text-[13px] transition-colors",
              tab === t.id ? "text-text" : "text-muted hover:text-text",
            )}
          >
            <span aria-hidden="true">{t.icon}</span>
            {t.label}
            {t.badge !== undefined && (
              <span className="flex min-w-4 items-center justify-center rounded-full bg-raised px-1 font-mono text-[10px] text-muted">
                {t.badge}
              </span>
            )}
            {tab === t.id && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-accent" />}
          </button>
        ))}
      </div>
      <div id="workspace-panel" role="tabpanel" aria-labelledby={`tab-${tab}`} className="relative min-h-0 flex-1">
        {tab === "graph" && <AgentGraph run={run} />}
        {tab === "timeline" && <Timeline run={run} />}
        {tab === "code" && <CodeView run={run} theme={theme} />}
        {tab === "checks" && <ChecksView run={run} />}
        {tab === "sources" && <SourcesView run={run} />}
        {tab === "ledger" && <LedgerView run={run} />}
        {mode === "live" && run.status !== "running" && run.status !== "completed" && (
          <LiveOverlay run={run} offline={offline} onPlaySample={onPlaySample} />
        )}
      </div>
      {mode === "sample" && <ReplayBar sample={sample} replay={replay} />}
    </section>
  );
}

function RunHeader({ run, mode }: { run: RunView; mode: "sample" | "live" }) {
  const elapsed = run.startedAt !== null ? (run.endedAt ?? run.lastAt ?? run.startedAt) - run.startedAt : 0;
  const iteration = run.iterations.at(-1)?.n;
  const status =
    run.status === "running"
      ? iteration && iteration > 1
        ? { tone: "warn" as const, text: "Self-healing" }
        : { tone: "accent" as const, text: "Running" }
      : run.status === "completed"
        ? { tone: "ok" as const, text: "Completed" }
        : run.status === "failed"
          ? { tone: "fail" as const, text: "Stopped" }
          : { tone: "neutral" as const, text: "No run" };
  return (
    <header className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border px-4 py-2.5">
      <div className="min-w-0 basis-full sm:basis-0 sm:flex-1">
        <p className="truncate text-sm font-medium text-text" title={run.request}>
          {run.request || "Integration runs appear here"}
        </p>
        <p className="text-[11px] text-faint">
          {run.systems.length ? run.systems.map((s) => PROVIDER_NAMES[s] ?? s).join(" → ") : "Agents, code, checks and sources, live"}
        </p>
      </div>
      {mode === "sample" && (
        <Badge tone="warn" title="Played from a recorded script that uses the real event format. The live agent graph arrives in Phase 4.">
          Scripted sample
        </Badge>
      )}
      <Badge tone={status.tone}>{status.text}</Badge>
      {iteration !== undefined && (
        <Badge title="Each failed check sends the run back through the diagnostician to the coder">
          <RotateCcw size={11} aria-hidden="true" /> Iteration {iteration}
        </Badge>
      )}
      <Badge title="Elapsed">
        <Timer size={11} aria-hidden="true" /> {clock(elapsed)}
      </Badge>
      <Badge title="Model spend for this run so far">
        <Coins size={11} aria-hidden="true" /> {usd(run.costUsd)}
      </Badge>
    </header>
  );
}

function LiveOverlay({ run, offline, onPlaySample }: { run: RunView; offline: boolean; onPlaySample: () => void }) {
  const notBuilt = run.status === "failed" && run.failure === "not_implemented";
  if (offline && !notBuilt) {
    return (
      <Overlay title="Live mode is offline" onPlaySample={onPlaySample}>
        The live backend runs on demand, and it isn&apos;t running right now. The sample run plays a complete
        run in the same event format the agents stream: every agent step, the code at each iteration, the checks
        and the sources.
      </Overlay>
    );
  }
  return (
    <Overlay title={notBuilt ? "The run was recorded, and stops here for now" : "No run yet"} onPlaySample={onPlaySample}>
      {notBuilt
        ? "The agents that research, write, test and fix the code are Phase 4 of this project. The run, its event stream and this workspace are already wired up. The sample run shows what they will stream."
        : "Ask for an integration in the chat and its run streams here: every agent step, the code at each iteration, the checks and the sources it used."}
    </Overlay>
  );
}

function Overlay({ title, children, onPlaySample }: { title: string; children: ReactNode; onPlaySample: () => void }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center bg-bg/70 p-6 backdrop-blur-[2px]">
      <div className="max-w-md rounded-xl border border-border bg-panel p-5 shadow-xl">
        <h3 className="text-base font-semibold text-text">{title}</h3>
        <p className="mt-2 text-sm text-muted">{children}</p>
        <Button variant="primary" className="mt-4" onClick={onPlaySample}>
          <Play size={14} aria-hidden="true" /> Watch the sample run
        </Button>
      </div>
    </div>
  );
}
