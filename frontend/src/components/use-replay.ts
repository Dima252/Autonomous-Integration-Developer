"use client";

import { useCallback, useEffect, useState } from "react";
import type { SampleRun } from "@/data/sample-run";
import { duration } from "@/lib/replay";
import type { ReplayControls } from "./workspace/replay-bar";

const TICK_MS = 50;

export function useReplay(sample: SampleRun): ReplayControls {
  const total = duration(sample);
  const [elapsed, setElapsed] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const running = playing && elapsed < total;

  useEffect(() => {
    if (!running) return;
    let last = performance.now();
    const id = setInterval(() => {
      const now = performance.now();
      const dt = (now - last) * speed;
      last = now;
      setElapsed((e) => Math.min(total, e + dt));
    }, TICK_MS);
    return () => clearInterval(id);
  }, [running, speed, total]);

  const play = useCallback(() => {
    setElapsed((e) => (e >= total ? 0 : e));
    setPlaying(true);
  }, [total]);
  const pause = useCallback(() => setPlaying(false), []);
  const restart = useCallback(() => {
    setElapsed(0);
    setPlaying(true);
  }, []);
  const seek = useCallback((ms: number) => setElapsed(Math.max(0, Math.min(total, ms))), [total]);

  return { elapsed, total, playing: running, speed, play, pause, restart, seek, setSpeed };
}
