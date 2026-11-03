// Records the README's media from the static build (ADR 018): an animated walkthrough
// of the sample run on the agent graph, and a still of each workspace view.
// Build first (`npm run build`), then `npm run media`. Writes to ../docs/images/.
//
// The page runs on Playwright's fake clock, and every frame is taken at a fixed point on
// the run's timeline, so a re-record differs only where the UI does.
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "@playwright/test";
import gifenc from "gifenc";
import { PNG } from "pngjs";

const { GIFEncoder, applyPalette, quantize } = gifenc; // CommonJS

const PORT = 4174;
const OUT = join(import.meta.dirname, "..", "..", "docs", "images");
const VIEWPORT = { width: 1440, height: 900 };
const GIF_VIEWPORT = { width: 1280, height: 800 };

const RUN_MS = 46_000; // the sample run's length, plus a beat to settle on "Completed"
const STEP_MS = 250; // run time between GIF frames
const FRAME_DELAY_MS = 70; // so the GIF plays at ~3.5× the run's real speed
const HOLD_LAST_MS = 3_000;

async function serve() {
  const server = spawn(process.execPath, [join(import.meta.dirname, "..", "e2e", "serve.mjs")], {
    env: { ...process.env, PORT: String(PORT) },
    stdio: ["ignore", "pipe", "inherit"],
  });
  await new Promise((resolve, reject) => {
    server.stdout.once("data", resolve);
    server.once("exit", (code) => reject(new Error(`static server exited (${code}); run npm run build first`)));
  });
  return server;
}

/** A page on the fake clock, the sample run held at 0. Reduced motion, because entry
 * animations run on the browser's animation clock, which the fake clock doesn't drive. */
async function open(browser, viewport) {
  const page = await browser.newPage({ viewport, colorScheme: "dark", reducedMotion: "reduce" });
  await page.clock.install({ time: new Date("2026-11-03T10:00:00Z") });
  await page.goto(`http://localhost:${PORT}/`);
  await page.getByRole("region", { name: "Run workspace" }).getByText("Scripted sample").waitFor();
  // Let the health probe settle, so the footer shows its offline copy.
  await page.getByText("Live mode runs on demand").waitFor();
  return page;
}

async function advance(page, ms) {
  await page.clock.runFor(ms);
  // React commits on the next frame; give it one real frame to paint.
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => r())));
}

function rgba(png) {
  const { data, width, height } = PNG.sync.read(png);
  return { data: new Uint8Array(data.buffer, data.byteOffset, data.length), width, height };
}

async function walkthrough(browser) {
  const page = await open(browser, GIF_VIEWPORT);
  const frames = [];
  for (let t = 0; t <= RUN_MS; t += STEP_MS) {
    frames.push(rgba(await page.screenshot()));
    await advance(page, STEP_MS);
  }
  await page.close();

  // One palette for the whole GIF, from frames spread over the run. The last index is
  // kept free to mark pixels that didn't change since the previous frame.
  const { width, height } = frames[0];
  const sample = frames.filter((_, i) => i % Math.ceil(frames.length / 12) === 0 || i === frames.length - 1);
  const pixels = new Uint8Array(sample.reduce((n, f) => n + f.data.length, 0));
  sample.reduce((off, f) => (pixels.set(f.data, off), off + f.data.length), 0);
  const colors = quantize(pixels, 255);
  const clear = colors.length;

  const gif = GIFEncoder();
  let prev = null;
  frames.forEach((frame, i) => {
    const index = applyPalette(frame.data, colors);
    const changed = prev ? index.map((c, p) => (c === prev[p] ? clear : c)) : index;
    gif.writeFrame(changed, width, height, {
      palette: i === 0 ? [...colors, [0, 0, 0]] : undefined,
      delay: i === frames.length - 1 ? HOLD_LAST_MS : FRAME_DELAY_MS,
      transparent: i > 0,
      transparentIndex: clear,
      dispose: 1, // keep the previous frame under the unchanged pixels
    });
    prev = index;
  });
  gif.finish();
  const bytes = gif.bytes();
  writeFileSync(join(OUT, "walkthrough.gif"), bytes);
  console.log(`walkthrough.gif: ${frames.length} frames, ${(bytes.length / 1e6).toFixed(1)} MB`);
}

async function stills(browser) {
  const page = await open(browser, VIEWPORT);
  const shot = async (name) => {
    await page.mouse.move(0, VIEWPORT.height - 1); // no hover highlight on the tabs
    await page.screenshot({ path: join(OUT, name) });
    console.log(name);
  };
  const tab = async (name) => {
    await page.getByRole("tab", { name }).click();
    await advance(page, 1_000);
  };

  await advance(page, RUN_MS);

  await tab(/Code/);
  await page.locator(".monaco-editor .view-lines").first().waitFor({ timeout: 30_000 });
  await advance(page, 1_000);
  await shot("code.png");
  await tab(/Checks/);
  await shot("checks.png");
  await tab(/Sources/);
  await shot("sources.png");
  await tab(/Spec/);
  await shot("spec.png");
  await page.close();
}

const server = await serve();
const browser = await chromium.launch();
try {
  await stills(browser);
  await walkthrough(browser);
} finally {
  await browser.close();
  server.kill();
}
