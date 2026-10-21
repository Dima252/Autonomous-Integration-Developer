"use client";

import { Pause, Play, RotateCcw } from "lucide-react";
import type { SampleRun } from "@/data/sample-run";
import { clock, cx } from "@/lib/format";
import { IconButton } from "../ui";

export interface ReplayControls {
  elapsed: number;
  total: number;
  playing: boolean;
  speed: number;
  play: () => void;
  pause: () => void;
  restart: () => void;
  seek: (ms: number) => void;
  setSpeed: (speed: number) => void;
}

export function ReplayBar({ sample, replay }: { sample: SampleRun; replay: ReplayControls }) {
  const marks = sample.events.filter((e) => e.type === "iteration.started" || e.type === "clarification.requested");
  return (
    <div className="flex items-center gap-2 border-t border-border bg-panel px-3 py-2">
      {replay.playing ? (
        <IconButton label="Pause" onClick={replay.pause}>
          <Pause size={16} />
        </IconButton>
      ) : (
        <IconButton label="Play" onClick={replay.play}>
          <Play size={16} />
        </IconButton>
      )}
      <IconButton label="Restart" onClick={replay.restart}>
        <RotateCcw size={15} />
      </IconButton>
      <span className="w-20 shrink-0 font-mono text-[11px] text-muted">
        {clock(replay.elapsed)} / {clock(replay.total)}
      </span>
      <div className="relative flex-1">
        <label htmlFor="scrubber" className="sr-only">
          Replay position
        </label>
        <input
          id="scrubber"
          type="range"
          min={0}
          max={replay.total}
          step={100}
          value={Math.round(replay.elapsed)}
          onChange={(e) => replay.seek(Number(e.target.value))}
          className="w-full accent-[var(--accent)]"
        />
        <div className="pointer-events-none absolute inset-x-0 -top-1.5 h-1" aria-hidden="true">
          {marks.map((m) => (
            <span
              key={m.t}
              title={m.label}
              className={cx(
                "absolute h-1.5 w-1.5 -translate-x-1/2 rounded-full",
                m.type === "iteration.started" ? "bg-warn" : "bg-accent",
              )}
              style={{ left: `${(m.t / replay.total) * 100}%` }}
            />
          ))}
        </div>
      </div>
      <div className="flex shrink-0 rounded-md border border-border" role="radiogroup" aria-label="Playback speed">
        {[1, 2, 4].map((s) => (
          <button
            key={s}
            role="radio"
            aria-checked={replay.speed === s}
            onClick={() => replay.setSpeed(s)}
            className={cx(
              "h-7 px-2 font-mono text-[11px]",
              replay.speed === s ? "bg-accent-soft text-text" : "text-muted hover:text-text",
            )}
          >
            {s}×
          </button>
        ))}
      </div>
    </div>
  );
}
