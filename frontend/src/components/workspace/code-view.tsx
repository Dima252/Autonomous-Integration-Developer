"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { FileCode, GitCompare, Lock } from "lucide-react";
import { cx } from "@/lib/format";
import { codeIterations, iterationVerdict, type RunView } from "@/lib/run-state";
import { StatusIcon } from "../ui";

// Monaco is browser-only and large; it loads on first use.
const Editor = dynamic(() => import("@monaco-editor/react").then((m) => m.default), {
  ssr: false,
  loading: () => <EditorPlaceholder />,
});
const DiffEditor = dynamic(() => import("@monaco-editor/react").then((m) => m.DiffEditor), {
  ssr: false,
  loading: () => <EditorPlaceholder />,
});

function EditorPlaceholder() {
  return <div className="h-full animate-pulse bg-bg" />;
}

const LANGUAGES: Record<string, string> = { py: "python", md: "markdown", txt: "plaintext", json: "json", toml: "ini" };
const languageOf = (path: string) => LANGUAGES[path.split(".").pop() ?? ""] ?? "plaintext";
const LOCKED = (path: string) => path.startsWith("tests/");

export function CodeView({ run, theme }: { run: RunView; theme: "dark" | "light" }) {
  const iterations = codeIterations(run);
  const [picked, setPicked] = useState<{ iteration: number | null; path: string | null; diff: boolean }>({
    iteration: null,
    path: null,
    diff: true,
  });

  if (!iterations.length) return <p className="p-6 text-sm text-faint">No code yet. The coder writes files after the tests are locked.</p>;

  // Follow the newest iteration until the reader picks one.
  const iteration = picked.iteration !== null && iterations.includes(picked.iteration) ? picked.iteration : iterations.at(-1)!;
  const files = run.files[iteration];
  const previous = iterations.filter((n) => n < iteration).at(-1);
  const before = previous !== undefined ? run.files[previous] : undefined;
  const paths = Object.keys(files).sort((a, b) => Number(LOCKED(a)) - Number(LOCKED(b)) || a.localeCompare(b));
  const changed = (p: string) => !before || before[p] !== files[p];
  const path = picked.path && files[picked.path] !== undefined ? picked.path : (paths.find((p) => before && changed(p)) ?? paths[0]);
  const showDiff = picked.diff && before !== undefined && changed(path);
  const options = {
    readOnly: true,
    minimap: { enabled: false },
    fontSize: 12.5,
    lineNumbersMinChars: 3,
    scrollBeyondLastLine: false,
    renderSideBySide: false,
    automaticLayout: true,
    padding: { top: 12 },
  } as const;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <span className="text-xs text-muted">Iteration</span>
        {iterations.map((n) => {
          const verdict = iterationVerdict(run, n);
          return (
            <button
              key={n}
              onClick={() => setPicked({ ...picked, iteration: n })}
              aria-pressed={n === iteration}
              className={cx(
                "flex h-7 items-center gap-1.5 rounded-md border px-2 text-xs font-medium",
                n === iteration ? "border-accent bg-accent-soft text-text" : "border-border text-muted hover:text-text",
              )}
            >
              {n}
              {verdict && <StatusIcon status={verdict} size={12} />}
            </button>
          );
        })}
        <label className="ml-auto flex items-center gap-1.5 text-xs text-muted">
          <input
            type="checkbox"
            checked={picked.diff}
            onChange={(e) => setPicked({ ...picked, diff: e.target.checked })}
            className="accent-[var(--accent)]"
          />
          <GitCompare size={13} aria-hidden="true" /> Changes since iteration {previous ?? "–"}
        </label>
      </div>
      <div className="flex min-h-0 flex-1">
        <ul className="w-48 shrink-0 overflow-y-auto border-r border-border py-2" aria-label="Files">
          {paths.map((p) => (
            <li key={p}>
              <button
                onClick={() => setPicked({ ...picked, path: p })}
                aria-current={p === path}
                className={cx(
                  "flex w-full items-center gap-2 px-3 py-1.5 text-left font-mono text-xs",
                  p === path ? "bg-accent-soft text-text" : "text-muted hover:bg-hover hover:text-text",
                )}
              >
                <FileCode size={13} aria-hidden="true" className="shrink-0" />
                <span className="truncate">{p}</span>
                {LOCKED(p) && <Lock size={11} className="ml-auto shrink-0 text-faint" aria-label="locked" />}
                {!LOCKED(p) && before && changed(p) && (
                  <span className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-warn" aria-label="changed" />
                )}
              </button>
            </li>
          ))}
        </ul>
        <div className="min-w-0 flex-1 bg-bg">
          {showDiff ? (
            <DiffEditor
              key={`${path}-${previous}-${iteration}`}
              original={before?.[path] ?? ""}
              modified={files[path]}
              // Models are keyed by file and iteration, and kept: disposing them on
              // remount races the diff widget ("TextModel got disposed before…").
              originalModelPath={`iteration-${previous}/${path}`}
              modifiedModelPath={`iteration-${iteration}/${path}`}
              keepCurrentOriginalModel
              keepCurrentModifiedModel
              language={languageOf(path)}
              theme={theme === "dark" ? "vs-dark" : "light"}
              options={options}
            />
          ) : (
            <Editor
              key={`${path}-${iteration}`}
              value={files[path]}
              language={languageOf(path)}
              theme={theme === "dark" ? "vs-dark" : "light"}
              options={options}
            />
          )}
        </div>
      </div>
      {LOCKED(path) && (
        <p className="flex items-center gap-1.5 border-t border-border px-3 py-1.5 text-[11px] text-faint">
          <Lock size={11} aria-hidden="true" /> Acceptance tests are written from the spec before the code and locked, so a fix can&apos;t weaken them.
        </p>
      )}
    </div>
  );
}
