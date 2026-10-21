"use client";

import { BookOpen, ExternalLink } from "lucide-react";
import { PROVIDER_NAMES, displayName } from "@/lib/agents";
import type { RunView } from "@/lib/run-state";
import { Badge } from "../ui";

const DOC_TYPES: Record<string, string> = {
  guide: "Guide",
  operation: "Endpoint",
  webhook_event: "Event",
  schema: "Schema",
  playbook: "Playbook",
};

export function SourcesView({ run }: { run: RunView }) {
  if (!run.retrievals.length)
    return <p className="p-6 text-sm text-faint">No research yet. Each researcher answers its questions from the indexed docs and API catalog.</p>;
  return (
    <ol className="h-full space-y-4 overflow-y-auto p-4">
      {run.retrievals.map((r, i) => (
        <li key={i} className="rounded-lg border border-border">
          <header className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
            <BookOpen size={14} className="text-accent" aria-hidden="true" />
            <span className="text-[13px] font-medium text-text">{r.query}</span>
            <span className="ml-auto text-[11px] text-faint">{displayName(r.node)}</span>
          </header>
          <ul>
            {r.hits.map((h, j) => (
              <li key={j} className="flex items-center gap-2 border-t border-border px-3 py-2 first:border-t-0">
                <Badge>{PROVIDER_NAMES[h.provider] ?? h.provider}</Badge>
                <Badge tone="neutral">{DOC_TYPES[h.doc_type] ?? h.doc_type}</Badge>
                <span className="min-w-0 flex-1 truncate text-xs text-muted" title={h.breadcrumb}>
                  {h.breadcrumb}
                </span>
                {h.url && (
                  <a
                    href={h.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    aria-label={`Open ${h.title}`}
                    className="rounded p-1 text-muted hover:bg-hover hover:text-text"
                  >
                    <ExternalLink size={13} />
                  </a>
                )}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ol>
  );
}
