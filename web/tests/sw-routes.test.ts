import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// sw-routes.js is a plain (non-module) script so it can be loaded by the
// service worker via `importScripts()` without a bundler. To exercise the
// same code from Vitest we read the file and evaluate it with a fake `self`,
// then pull the routing helpers off `self.CS_ROUTES` — this is the
// documented alternative to a dual ESM/UMD export for this file.
const here = path.dirname(fileURLToPath(import.meta.url));
const src = readFileSync(path.resolve(here, "../public/sw-routes.js"), "utf8");
const sandbox: {
  CS_ROUTES?: {
    CACHE: string;
    PRECACHE: string[];
    classify: (r: { url: string; mode: string; method: string }) => string;
    shouldCache: (r: { ok: boolean; status: number } | undefined | null) => boolean;
  };
} = {};
new Function("self", src)(sandbox);
const { CACHE, PRECACHE, classify, shouldCache } = sandbox.CS_ROUTES!;

describe("sw routes", () => {
  it("never touches the API or non-GET", () => {
    expect(classify({ url: "https://x/api/recommend", mode: "cors", method: "POST" })).toBe("bypass");
    expect(classify({ url: "https://x/api/me", mode: "cors", method: "GET" })).toBe("bypass");
    expect(classify({ url: "https://x/", mode: "navigate", method: "POST" })).toBe("bypass");
  });
  it("classifies navigations and static assets", () => {
    expect(classify({ url: "https://x/me", mode: "navigate", method: "GET" })).toBe("navigate");
    expect(classify({ url: "https://x/_next/static/chunks/a.js", mode: "no-cors", method: "GET" })).toBe("static");
    expect(classify({ url: "https://x/icons/icon-192.png", mode: "no-cors", method: "GET" })).toBe("static");
    expect(classify({ url: "https://x/some.json", mode: "cors", method: "GET" })).toBe("bypass");
  });
  it("precache list includes the offline page and the cache is versioned", () => {
    expect(PRECACHE).toContain("/offline");
    expect(CACHE).toMatch(/^cs-shell-v\d+$/);
  });
});

describe("shouldCache", () => {
  it("caches ok responses", () => {
    expect(shouldCache({ ok: true, status: 200 })).toBe(true);
    expect(shouldCache({ ok: true, status: 304 })).toBe(true);
  });
  it("never caches error responses", () => {
    expect(shouldCache({ ok: false, status: 404 })).toBe(false);
    expect(shouldCache({ ok: false, status: 500 })).toBe(false);
    // Belt and suspenders: even if `ok` were somehow true, a >= 400 status must not be cached.
    expect(shouldCache({ ok: true, status: 404 })).toBe(false);
  });
  it("never caches a missing response", () => {
    expect(shouldCache(undefined)).toBe(false);
    expect(shouldCache(null)).toBe(false);
  });
});
