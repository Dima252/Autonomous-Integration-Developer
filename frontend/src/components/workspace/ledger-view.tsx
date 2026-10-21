"use client";

import type { ReactNode } from "react";
import { CircleHelp, GraduationCap, Lightbulb, Scale } from "lucide-react";
import type { RunView } from "@/lib/run-state";

function Section({ icon, title, hint, children }: { icon: ReactNode; title: string; hint: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <header>
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-text">
          {icon} {title}
        </h3>
        <p className="text-xs text-faint">{hint}</p>
      </header>
      {children}
    </section>
  );
}

export function LedgerView({ run }: { run: RunView }) {
  const empty = !run.questions.length && !run.assumptions.length && !run.decisions.length && !run.lessons.length;
  if (empty) return <p className="p-6 text-sm text-faint">The analyst&apos;s questions and assumptions and the architect&apos;s decisions appear here.</p>;
  return (
    <div className="h-full space-y-6 overflow-y-auto p-4">
      {run.questions.length > 0 && (
        <Section
          icon={<CircleHelp size={15} className="text-accent" aria-hidden="true" />}
          title="Asked"
          hint="Only questions whose answer changes the code: one round, at most three."
        >
          <ul className="space-y-2">
            {run.questions.map((q) => (
              <li key={q} className="rounded-lg border border-border bg-raised px-3 py-2 text-[13px] text-text">
                {q}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {run.assumptions.length > 0 && (
        <Section
          icon={<Lightbulb size={15} className="text-warn" aria-hidden="true" />}
          title="Assumed"
          hint="Everything else is assumed and recorded, so you can override it."
        >
          <ul className="space-y-2">
            {run.assumptions.map((a) => (
              <li key={a.text} className="flex items-start gap-3 rounded-lg border border-border px-3 py-2">
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] text-text">{a.text}</p>
                  {a.reason && <p className="mt-0.5 text-xs text-muted">{a.reason}</p>}
                </div>
                {a.overridable && (
                  <button
                    disabled
                    title="Overrides re-run only the affected stages. They arrive with the agent graph (Phase 3)."
                    className="shrink-0 rounded-md border border-border px-2 py-1 text-[11px] text-muted disabled:opacity-60"
                  >
                    Override
                  </button>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {run.decisions.length > 0 && (
        <Section
          icon={<Scale size={15} className="text-accent" aria-hidden="true" />}
          title="Decided"
          hint="Design decisions with the alternatives considered and why."
        >
          <ul className="space-y-2">
            {run.decisions.map((d) => (
              <li key={d.decision} className="rounded-lg border border-border px-3 py-2">
                <p className="text-[13px] font-medium text-text">{d.decision}</p>
                {d.alternatives.length > 0 && (
                  <p className="mt-0.5 text-xs text-faint">Instead of: {d.alternatives.join(", ")}</p>
                )}
                {d.rationale && <p className="mt-1 text-xs text-muted">{d.rationale}</p>}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {run.lessons.length > 0 && (
        <Section
          icon={<GraduationCap size={15} className="text-ok" aria-hidden="true" />}
          title="Learned"
          hint="Saved by the memory curator and read by the coder on later runs."
        >
          <ul className="space-y-2">
            {run.lessons.map((l) => (
              <li key={l} className="rounded-lg border border-border px-3 py-2 text-[13px] text-text">
                {l}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}
