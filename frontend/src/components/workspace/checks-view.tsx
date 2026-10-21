"use client";

import { useState } from "react";
import { CHECKS } from "@/lib/agents";
import { cx } from "@/lib/format";
import type { CheckResult, RunView } from "@/lib/run-state";
import type { CheckName } from "@/lib/types";
import { StatusIcon } from "../ui";

export function ChecksView({ run }: { run: RunView }) {
  const iterations = Object.keys(run.checks).map(Number).sort((a, b) => a - b);
  const [picked, setPicked] = useState<{ iteration: number; check: CheckName } | null>(null);

  if (!iterations.length)
    return <p className="p-6 text-sm text-faint">No checks yet. The validator runs them after each attempt, cheapest and most decisive first.</p>;

  const latestFailure = [...iterations]
    .reverse()
    .flatMap((n) => CHECKS.map((c) => ({ iteration: n, check: c.id as CheckName })))
    .find((k) => run.checks[k.iteration]?.[k.check]?.status === "fail");
  const selected = picked ?? latestFailure ?? null;
  const result: CheckResult | undefined = selected ? run.checks[selected.iteration]?.[selected.check] : undefined;

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto p-4">
      <div className="shrink-0 overflow-x-auto rounded-lg border border-border">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Check results per iteration</caption>
          <thead>
            <tr className="bg-raised text-left text-xs text-muted">
              <th scope="col" className="px-3 py-2 font-medium">Check</th>
              {iterations.map((n) => (
                <th key={n} scope="col" className="px-3 py-2 text-center font-medium">
                  Iteration {n}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {CHECKS.map((c) => (
              <tr key={c.id} className="border-t border-border">
                <th scope="row" className="px-3 py-2 text-left font-normal">
                  <span className="block text-[13px] text-text">{c.name}</span>
                  <span className="block text-[11px] text-faint">{c.detail}</span>
                </th>
                {iterations.map((n) => {
                  const r = run.checks[n]?.[c.id as CheckName];
                  const isSelected = selected?.iteration === n && selected.check === c.id;
                  return (
                    <td key={n} className="px-2 py-1.5 text-center">
                      {r ? (
                        <button
                          onClick={() => setPicked({ iteration: n, check: c.id as CheckName })}
                          aria-label={`${c.name}, iteration ${n}: ${r.status}`}
                          aria-pressed={isSelected}
                          className={cx(
                            "mx-auto flex h-8 w-full max-w-28 items-center justify-center gap-1.5 rounded-md border text-xs",
                            r.status === "pass" && "border-transparent bg-ok-soft text-ok",
                            r.status === "fail" && "border-transparent bg-fail-soft text-fail",
                            r.status === "skip" && "border-border text-faint",
                            isSelected && "ring-2 ring-accent",
                          )}
                        >
                          <StatusIcon status={r.status} size={13} />
                          {r.status}
                        </button>
                      ) : (
                        <span className="text-faint" aria-label="not run">
                          ·
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected && result && (
        <section className="mt-4 shrink-0 rounded-lg border border-border" aria-label="Check output">
          <header className="flex items-center gap-2 border-b border-border px-3 py-2 text-sm">
            <StatusIcon status={result.status} size={14} />
            <span className="font-medium text-text">{CHECKS.find((c) => c.id === selected.check)?.name}</span>
            <span className="text-muted">· iteration {selected.iteration} · {result.summary}</span>
          </header>
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap bg-bg p-3 font-mono text-[11.5px] leading-relaxed text-muted">
            {result.output ?? result.summary}
          </pre>
        </section>
      )}
    </div>
  );
}
