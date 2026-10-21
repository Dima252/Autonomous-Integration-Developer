import { describe, expect, it } from "vitest";
import { SAMPLE_RUN } from "@/data/sample-run";
import { duration, eventsAt, messagesAt, scriptedEvents } from "./replay";
import { codeIterations, foldRun, iterationVerdict } from "./run-state";

const events = scriptedEvents(SAMPLE_RUN);

describe("foldRun on the sample run", () => {
  const run = foldRun(events);

  it("ends completed with every agent done", () => {
    expect(run.status).toBe("completed");
    expect(run.activeNode).toBeNull();
    expect(Object.values(run.nodes).every((n) => n.status === "done")).toBe(true);
    expect(run.researchers).toEqual(["researcher:stripe", "researcher:slack"]);
  });

  it("tracks the self-healing loop", () => {
    expect(run.iterations.map((i) => i.n)).toEqual([1, 2, 3]);
    expect(run.nodes.coder.runs).toBe(3);
    expect([1, 2, 3].map((n) => iterationVerdict(run, n))).toEqual(["fail", "fail", "pass"]);
    expect(run.checks[1].pyright?.status).toBe("fail");
    expect(run.checks[2].pytest?.output).toContain("SignatureVerificationError");
  });

  it("keeps a full file snapshot per iteration", () => {
    expect(codeIterations(run)).toEqual([1, 2, 3]);
    // Iteration 2 only changed requirements.txt but still has every file.
    expect(Object.keys(run.files[2]).sort()).toEqual(Object.keys(run.files[1]).sort());
    expect(run.files[2]["requirements.txt"]).toContain("stripe");
    expect(run.files[1]["requirements.txt"]).not.toContain("stripe");
    expect(run.files[3]["app/main.py"]).toContain("await request.body()");
    expect(run.files[3]["tests/test_webhook.py"]).toBe(run.files[1]["tests/test_webhook.py"]);
  });

  it("collects the ledger, sources and cost", () => {
    expect(run.questions).toHaveLength(1);
    expect(run.assumptions).toHaveLength(2);
    expect(run.decisions).toHaveLength(3);
    expect(run.lessons).toHaveLength(2);
    expect(run.retrievals.length).toBeGreaterThanOrEqual(5);
    expect(run.costUsd).toBeCloseTo(0.0412);
  });
});

describe("foldRun mid-run", () => {
  it("marks the running step and a failed validation", () => {
    const failedAt = events.findIndex((e) => e.type === "step.completed" && e.data.ok === false);
    const run = foldRun(events.slice(0, failedAt + 1));
    expect(run.status).toBe("running");
    expect(run.nodes.validator.status).toBe("failed");
    expect(run.nodes.reviewer).toBeUndefined();
  });

  it("reports a run the backend can't execute yet", () => {
    const run = foldRun([
      { run_id: "r", seq: 1, ts: "2026-10-21T12:00:00Z", type: "run.created", label: "Run created", node: null, data: { request: "x", systems: ["stripe"] } },
      { run_id: "r", seq: 2, ts: "2026-10-21T12:00:01Z", type: "run.failed", label: "Not built yet", node: null, data: { reason: "not_implemented" } },
    ]);
    expect(run.status).toBe("failed");
    expect(run.failure).toBe("not_implemented");
    expect(run.systems).toEqual(["stripe"]);
  });
});

describe("replay", () => {
  it("reveals events and messages by time", () => {
    expect(eventsAt(SAMPLE_RUN, -1)).toBe(0);
    expect(eventsAt(SAMPLE_RUN, duration(SAMPLE_RUN))).toBe(SAMPLE_RUN.events.length);
    const early = messagesAt(SAMPLE_RUN, 800);
    expect(early.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(early[1].streaming).toBe(true);
    expect(early[1].content.length).toBeLessThan(SAMPLE_RUN.messages[1].content.length);
    const done = messagesAt(SAMPLE_RUN, duration(SAMPLE_RUN));
    expect(done.every((m) => !m.streaming)).toBe(true);
  });

  it("scripts events in time order", () => {
    const times = SAMPLE_RUN.events.map((e) => e.t);
    expect([...times].sort((a, b) => a - b)).toEqual(times);
  });
});
