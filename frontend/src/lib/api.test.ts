import { afterEach, describe, expect, it, vi } from "vitest";
import { checkApi } from "./api";

function respond(body: string, status = 200, type = "application/json") {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status, headers: { "content-type": type } })));
}

describe("checkApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is online when the API answers its health check", async () => {
    respond('{"status":"ok"}');
    expect(await checkApi()).toBe(true);
  });

  it("is offline behind CloudFront with no API (403 error page)", async () => {
    respond("<?xml version='1.0'?><Error><Code>AccessDenied</Code></Error>", 403, "application/xml");
    expect(await checkApi()).toBe(false);
  });

  it("is offline when a host answers every path with the HTML app", async () => {
    respond("<!doctype html><html></html>", 200, "text/html");
    expect(await checkApi()).toBe(false);
  });

  it("is offline when nothing answers", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    expect(await checkApi()).toBe(false);
  });
});
