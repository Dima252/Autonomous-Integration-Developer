"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Coins, Command, MessageSquare, Moon, Network, Play, Radio, Sun } from "lucide-react";
import { SAMPLE_RUN } from "@/data/sample-run";
import { cx, usd } from "@/lib/format";
import { eventsAt, messagesAt, scriptedEvents } from "@/lib/replay";
import { foldRun } from "@/lib/run-state";
import { ChatPanel, EXAMPLES } from "./chat/chat-panel";
import { CommandPalette, type PaletteAction } from "./command-palette";
import { Badge, IconButton, Segmented } from "./ui";
import { useLiveChat } from "./use-live-chat";
import { useReplay } from "./use-replay";
import { Workspace, type Tab } from "./workspace/workspace";

type Mode = "sample" | "live";
type Theme = "dark" | "light";

const SAMPLE_EVENTS = scriptedEvents(SAMPLE_RUN);
const REPO_URL = "https://github.com/Dima252/Autonomous-Integration-Developer";

function readTheme(): Theme {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

export function MissionControl() {
  const [mode, setMode] = useState<Mode>("sample");
  const [tab, setTab] = useState<Tab>("graph");
  const [theme, setTheme] = useState<Theme>("dark");
  const [pane, setPane] = useState<"chat" | "workspace">("chat"); // small screens only
  const [palette, setPalette] = useState(false);
  const replay = useReplay(SAMPLE_RUN);
  const live = useLiveChat();
  const { play } = replay;

  // The theme script in <head> may have switched to light before hydration.
  useEffect(() => {
    const id = requestAnimationFrame(() => setTheme(readTheme()));
    return () => cancelAnimationFrame(id);
  }, []);

  // First visit: the sample run starts by itself.
  useEffect(() => {
    const id = setTimeout(play, 700);
    return () => clearTimeout(id);
  }, [play]);

  const sampleCount = eventsAt(SAMPLE_RUN, replay.elapsed);
  const sampleRun = useMemo(() => foldRun(SAMPLE_EVENTS.slice(0, sampleCount), SAMPLE_RUN.id), [sampleCount]);
  const liveRun = useMemo(() => foldRun(live.runEvents, live.runId), [live.runEvents, live.runId]);
  const run = mode === "sample" ? sampleRun : liveRun;
  const messages = mode === "sample" ? messagesAt(SAMPLE_RUN, replay.elapsed) : live.messages;
  const sessionCost = messages.reduce((sum, m) => sum + (m.costUsd ?? 0), 0);

  const toggleTheme = useCallback(() => {
    const next: Theme = readTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("theme", next);
    } catch {
      // storage unavailable: the choice lasts for this page only
    }
    setTheme(next);
  }, []);

  const playSample = useCallback(() => {
    setMode("sample");
    setTab("graph");
    replay.restart();
  }, [replay]);

  const goLive = useCallback(() => {
    replay.pause();
    setMode("live");
    setPane("chat");
  }, [replay]);

  const send = useCallback(
    (text: string) => {
      if (mode !== "live") goLive();
      void live.send(text);
    },
    [mode, goLive, live],
  );

  const openRun = useCallback(() => {
    setTab("graph");
    setPane("workspace");
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const actions: PaletteAction[] = [
    { id: "sample", group: "Run", label: "Play the sample run from the start", run: playSample },
    { id: "live", group: "Run", label: "Switch to live chat", run: goLive },
    { id: "new", group: "Run", label: "New live conversation", run: () => { live.reset(); goLive(); } },
    ...(["graph", "timeline", "code", "checks", "sources", "ledger"] as Tab[]).map((t) => ({
      id: `tab-${t}`,
      group: "View",
      label: `Open ${t === "ledger" ? "spec & decisions" : t === "graph" ? "agents" : t}`,
      run: () => {
        setTab(t);
        setPane("workspace");
      },
    })),
    { id: "theme", group: "View", label: `Switch to ${theme === "dark" ? "light" : "dark"} theme`, run: toggleTheme },
    // Without a backend there's nothing to ask: the Live tab explains that instead.
    ...(live.online === false
      ? []
      : EXAMPLES.map((e, i) => ({ id: `ex-${i}`, group: "Ask (live)", label: e, run: () => send(e) }))),
  ];

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-panel px-3 sm:px-4">
        <Logo />
        <div className="hidden min-w-0 sm:block">
          <h1 className="truncate text-sm font-semibold text-text">Autonomous Integration Developer</h1>
          <p className="hidden truncate text-[11px] text-faint sm:block">Researches the docs, writes the code, tests it, fixes itself</p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Segmented<Mode>
            label="Mode"
            value={mode}
            onChange={(m) => (m === "live" ? goLive() : playSample())}
            options={[
              {
                value: "sample",
                label: (
                  <>
                    <Play size={12} aria-hidden="true" />
                    <span>
                      Sample<span className="hidden sm:inline"> run</span>
                    </span>
                  </>
                ),
              },
              { value: "live", label: <><Radio size={12} aria-hidden="true" /> Live</> },
            ]}
          />
          <span className="hidden md:block">
            <Badge title="Model spend in this conversation">
              <Coins size={11} aria-hidden="true" /> {usd(sessionCost)}
            </Badge>
          </span>
          <span className="hidden sm:block">
            <IconButton label="Commands (Ctrl+K)" onClick={() => setPalette(true)}>
              <Command size={16} />
            </IconButton>
          </span>
          <IconButton label={theme === "dark" ? "Light theme" : "Dark theme"} onClick={toggleTheme}>
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </IconButton>
          <a
            href={REPO_URL}
            target="_blank"
            rel="noreferrer noopener"
            aria-label="Source code on GitHub"
            title="Source code on GitHub"
            className="hidden h-9 w-9 items-center justify-center rounded-md text-muted hover:bg-hover hover:text-text sm:inline-flex"
          >
            <GitHubMark />
          </a>
        </div>
      </header>

      <nav aria-label="Panes" className="flex border-b border-border lg:hidden">
        {(["chat", "workspace"] as const).map((p) => (
          <button
            key={p}
            onClick={() => setPane(p)}
            aria-pressed={pane === p}
            className={cx(
              "flex h-10 flex-1 items-center justify-center gap-1.5 text-sm",
              pane === p ? "border-b-2 border-accent text-text" : "text-muted",
            )}
          >
            {p === "chat" ? <MessageSquare size={14} aria-hidden="true" /> : <Network size={14} aria-hidden="true" />}
            {p === "chat" ? "Chat" : "Workspace"}
          </button>
        ))}
      </nav>

      <main className="flex min-h-0 flex-1">
        <div
          className={cx(
            "min-h-0 w-full border-border bg-panel lg:block lg:w-[400px] lg:shrink-0 lg:border-r xl:w-[440px]",
            pane === "chat" ? "block" : "hidden",
          )}
        >
          <ChatPanel
            mode={mode}
            online={live.online}
            messages={messages}
            sending={live.sending}
            onSend={send}
            onOpenRun={openRun}
            onGoLive={goLive}
            onPlaySample={playSample}
          />
        </div>
        <div className={cx("min-h-0 min-w-0 flex-1 lg:block", pane === "workspace" ? "block" : "hidden")}>
          <Workspace
            run={run}
            tab={tab}
            onTab={setTab}
            theme={theme}
            mode={mode}
            offline={live.online === false}
            sample={SAMPLE_RUN}
            replay={replay}
            onPlaySample={playSample}
          />
        </div>
      </main>

      <CommandPalette open={palette} onClose={() => setPalette(false)} actions={actions} />
    </div>
  );
}

function Logo() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true" className="shrink-0">
      <rect width="28" height="28" rx="7" fill="var(--accent-soft)" />
      <circle cx="9" cy="9" r="2.5" fill="var(--accent)" />
      <circle cx="19" cy="9" r="2.5" fill="var(--accent)" />
      <circle cx="14" cy="19" r="2.5" fill="var(--ok)" />
      <path d="M9 9 L19 9 L14 19 Z" fill="none" stroke="var(--accent)" strokeWidth="1.4" />
    </svg>
  );
}

function GitHubMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}
