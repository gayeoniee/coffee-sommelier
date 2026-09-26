#!/usr/bin/env node
// Records the guest onboarding → brand recommendation → log a drink → my taste flow
// on an iPhone-sized viewport and encodes it as a small GIF for the README.
//
// Usage: node scripts/demo-gif.mjs [--base http://localhost:3000] [--out ../docs/demo.gif]
//
// Requires a running backend (COOKIE_SECURE=false uv run uvicorn app.api:get_app --factory --port 8000)
// and a running `npm run dev` (or `npm run build && npm run start`) web server.

import { chromium, devices } from "@playwright/test";
import gifenc from "gifenc";
import { PNG } from "pngjs";

const { GIFEncoder, quantize, applyPalette } = gifenc;
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const BASE = arg("base", "http://localhost:3000");
const OUT = path.resolve(process.cwd(), arg("out", "../docs/demo.gif"));
const FRAMES_DIR = arg("frames-dir", null); // optional: also dump every captured PNG here for inspection

const CAPTURE_INTERVAL_MS = 400;
const MAX_FRAMES = 70;
const MAX_GIF_BYTES = 6 * 1024 * 1024;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForCount(locator, count, timeoutMs) {
  const start = Date.now();
  for (;;) {
    if ((await locator.count()) === count) return;
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for count=${count}`);
    await sleep(200);
  }
}

async function waitForGone(page, text, timeoutMs) {
  const start = Date.now();
  for (;;) {
    if ((await page.getByText(text).count()) === 0) return;
    if (Date.now() - start > timeoutMs) throw new Error(`timed out waiting for "${text}" to disappear`);
    await sleep(300);
  }
}

async function waitForVisible(locatorOrPage, textOrLocator, timeoutMs) {
  const locator = typeof textOrLocator === "string" ? locatorOrPage.getByText(textOrLocator) : textOrLocator;
  const start = Date.now();
  for (;;) {
    if ((await locator.count()) > 0) return;
    if (Date.now() - start > timeoutMs) throw new Error("timed out waiting for element to appear");
    await sleep(200);
  }
}

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    ...devices["iPhone 13"],
    // Capture at CSS pixel width (390px) directly instead of the device's 3x pixel ratio —
    // this is the "downscale to 390px" step, done up front so no image-resize dependency is needed.
    deviceScaleFactor: 1,
    baseURL: BASE,
  });
  const page = await context.newPage();

  const frames = []; // { buffer: Buffer(png), t: number }
  let capturing = true;

  async function captureLoop() {
    while (capturing) {
      try {
        const buffer = await page.screenshot({ type: "png" });
        frames.push({ buffer, t: Date.now() });
      } catch (e) {
        // page may be mid-navigation; skip this tick
        if (process.env.DEMO_GIF_DEBUG) console.error("screenshot skip:", e.message);
      }
      await sleep(CAPTURE_INTERVAL_MS);
    }
  }
  const captureTask = captureLoop();

  // Most of these UI transitions are instant (no network round trip), which means the ~400ms
  // background capture loop can easily land zero frames on a screen that only exists for a few
  // milliseconds. `beat()` holds on the current screen long enough to guarantee it gets sampled,
  // and takes an extra on-the-spot screenshot so the screen is captured even if the interval timer
  // doesn't tick during the hold.
  async function beat(ms = 700) {
    try {
      frames.push({ buffer: await page.screenshot({ type: "png" }), t: Date.now() });
    } catch (e) {
      if (process.env.DEMO_GIF_DEBUG) console.error("beat screenshot skip:", e.message);
    }
    await sleep(ms);
  }

  try {
    await page.goto("/");
    await page.waitForURL("**/onboarding");

    // Step 1: 꼭 지켜야 할 조건
    await beat(); // show the caffeine-rule screen before picking anything
    await page.getByRole("radio", { name: "디카페인만" }).click();
    await beat(500); // show the selected radio
    await page.getByRole("button", { name: "다음" }).click();

    // Step 2: 어떤 맛을 좋아하세요
    await beat();
    await page.getByRole("button", { name: "과일" }).click();
    await beat(500); // show the selected chip
    await page.getByRole("button", { name: "다음" }).click();

    // Step 3: 샘플 (건너뛰어도 됨) → 시작하기
    await waitForVisible(page, page.getByRole("button", { name: "시작하기" }), 15_000);
    await beat();
    await page.getByRole("button", { name: "시작하기" }).click();
    await page.waitForURL((u) => u.pathname === "/");
    await beat(); // home screen, before a brand is picked

    // Home → 할리스 추천
    await page.getByRole("button", { name: "할리스" }).click();
    const cards = page.locator("article");
    await waitForCount(cards, 3, 30_000);
    await waitForGone(page, "설명을 쓰는 중…", 45_000);
    await beat(); // all 3 cards with finished explanations

    // 마셔봤어요 → 4점 → 저장
    await cards.first().getByRole("button", { name: "마셔봤어요" }).click();
    await beat(500); // empty rating sheet
    await page.getByRole("button", { name: "4점" }).click();
    await beat(400); // 4 stars filled in
    await page.getByRole("button", { name: "저장" }).click();
    await waitForVisible(page, "기록했어요", 30_000);
    await beat(700); // let the "기록했어요" summary sit on screen for a moment
    await page.getByRole("button", { name: "닫기" }).click();

    // 내 취향
    await beat(400);
    await page.getByRole("link", { name: "내 취향" }).click();
    await waitForVisible(page, /기록 1회/, 15_000).catch(() => {});
    await beat(1200); // hold on the final screen so it's visible in the GIF
  } finally {
    capturing = false;
    await captureTask;
  }

  await browser.close();

  console.log(`captured ${frames.length} raw frames`);
  if (frames.length === 0) throw new Error("no frames captured");

  // Downsample to at most MAX_FRAMES, always keeping the first and last frame, so the GIF still
  // covers the whole flow (onboarding → recommend → log → my taste) even if the run took longer
  // than MAX_FRAMES * CAPTURE_INTERVAL_MS (e.g. slow LLM explanation streaming).
  let picked = frames;
  if (frames.length > MAX_FRAMES) {
    picked = [];
    for (let i = 0; i < MAX_FRAMES; i++) {
      const idx = Math.round((i * (frames.length - 1)) / (MAX_FRAMES - 1));
      picked.push(frames[idx]);
    }
  }

  // Save first/middle/last frames as PNG for a manual sanity check.
  const scratchDir = arg("scratch-dir", path.resolve(__dirname, "..", "..", ".demo-gif-check"));
  fs.mkdirSync(scratchDir, { recursive: true });
  const sampleIdx = { first: 0, middle: Math.floor(picked.length / 2), last: picked.length - 1 };
  for (const [label, idx] of Object.entries(sampleIdx)) {
    fs.writeFileSync(path.join(scratchDir, `${label}.png`), picked[idx].buffer);
  }
  console.log(`sample frames written to ${scratchDir}`);

  if (FRAMES_DIR) {
    fs.mkdirSync(FRAMES_DIR, { recursive: true });
    picked.forEach((f, i) => fs.writeFileSync(path.join(FRAMES_DIR, `frame-${String(i).padStart(3, "0")}.png`), f.buffer));
  }

  // Decode every picked frame to raw RGBA.
  const decoded = picked.map((f) => PNG.sync.read(f.buffer));
  const { width, height } = decoded[0];

  // Build one global palette from all frames combined so colors stay stable across the GIF.
  const combined = Buffer.concat(decoded.map((d) => d.data));
  const palette = quantize(combined, 256);

  const gif = GIFEncoder();
  for (let i = 0; i < decoded.length; i++) {
    const index = applyPalette(decoded[i].data, palette);
    const nextT = picked[i + 1]?.t ?? picked[i].t + CAPTURE_INTERVAL_MS;
    const delay = Math.max(50, nextT - picked[i].t);
    gif.writeFrame(index, width, height, { palette, delay });
  }
  gif.finish();
  const bytes = gif.bytes();

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, bytes);

  const totalDurationMs = picked[picked.length - 1].t - picked[0].t;
  console.log(`wrote ${OUT}`);
  console.log(`frames: ${picked.length}, size: ${(bytes.length / 1024 / 1024).toFixed(2)} MB, duration: ~${(totalDurationMs / 1000).toFixed(1)}s`);

  if (bytes.length > MAX_GIF_BYTES) {
    throw new Error(`GIF is ${(bytes.length / 1024 / 1024).toFixed(2)} MB, exceeds ${MAX_GIF_BYTES / 1024 / 1024} MB limit`);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
