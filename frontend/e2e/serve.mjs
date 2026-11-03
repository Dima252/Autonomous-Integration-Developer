// Serves the static export (out/) with no API behind it: files, 404.html for unknown
// paths, and an error page for /api/*, which the UI must read as "live mode offline".
// The e2e tests and `npm run media` both run against it.
import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize } from "node:path";

const ROOT = join(import.meta.dirname, "..", "out");
const PORT = Number(process.env.PORT ?? 4173);
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".txt": "text/plain; charset=utf-8",
  ".json": "application/json",
  ".woff2": "font/woff2",
};

function file(path) {
  const full = join(ROOT, normalize(path).replace(/^([/\\])+/, ""));
  if (!full.startsWith(ROOT)) return null;
  if (existsSync(full) && statSync(full).isFile()) return full;
  for (const candidate of [join(full, "index.html"), `${full}.html`]) {
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

createServer((req, res) => {
  const path = decodeURIComponent(new URL(req.url ?? "/", "http://x").pathname);
  if (path.startsWith("/api/")) {
    res.writeHead(403, { "content-type": "application/xml" });
    res.end("<?xml version='1.0' encoding='UTF-8'?><Error><Code>AccessDenied</Code></Error>");
    return;
  }
  const found = file(path);
  const status = found ? 200 : 404;
  const target = found ?? join(ROOT, "404.html");
  res.writeHead(status, { "content-type": TYPES[extname(target)] ?? "application/octet-stream" });
  createReadStream(target).pipe(res);
}).listen(PORT, () => console.log(`serving ${ROOT} on http://localhost:${PORT}`));
