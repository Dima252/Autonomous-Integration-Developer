import { describe, expect, it } from "vitest";
import { SseParser } from "./sse";

describe("SseParser", () => {
  it("parses named events with ids", () => {
    const parser = new SseParser();
    const out = parser.push('event: route\nid: 7-0\ndata: {"intent":"chitchat"}\n\n');
    expect(out).toEqual([{ event: "route", id: "7-0", data: '{"intent":"chitchat"}' }]);
  });

  it("buffers events split across chunks", () => {
    const parser = new SseParser();
    expect(parser.push("event: delta\nda")).toEqual([]);
    expect(parser.push('ta: {"text":"hi"}\n')).toEqual([]);
    expect(parser.push("\nevent: done\ndata: {}\n\n")).toEqual([
      { event: "delta", id: undefined, data: '{"text":"hi"}' },
      { event: "done", id: undefined, data: "{}" },
    ]);
  });

  it("skips keep-alive comments and joins multi-line data", () => {
    const parser = new SseParser();
    const out = parser.push(": ping\n\ndata: line one\r\ndata: line two\r\n\r\n");
    expect(out).toEqual([{ event: "message", id: undefined, data: "line one\nline two" }]);
  });
});
