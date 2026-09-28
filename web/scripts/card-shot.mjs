#!/usr/bin/env node
// Screenshots one recommendation card from the live submission site for the competition draft
// (docs/competition/images/05_product_card.png): guest onboarding as a decaf-only guest who likes
// fruity coffee → a brand → the first card, after its explanation has finished streaming.
//
// Usage: node scripts/card-shot.mjs [--base https://coffee-sommelier-open.vercel.app] [--brand 메가MGC커피 | --analyze "원두 문구"]
//                                   [--out ../../docs/competition/images/05_product_card.png]
// --out is resolved relative to this script's directory (like demo-gif.mjs).

import { chromium, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
};
const BASE = arg("base", "https://coffee-sommelier-open.vercel.app");
const BRAND = arg("brand", "메가MGC커피");
const ANALYZE = arg("analyze", null); // e.g. "에티오피아 예가체프 워시드 디카페인" -> the 개인 카페 tab instead of a brand
const OUT = path.resolve(__dirname, arg("out", "../../docs/competition/images/05_product_card.png"));
const PAGE_OUT = OUT.replace(/\.png$/, "_page.png");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ ...devices["iPhone 13"], deviceScaleFactor: 2, baseURL: BASE });
  const page = await context.newPage();
  try {
    await page.goto("/", { timeout: 120_000 });
    await page.waitForURL("**/onboarding", { timeout: 120_000 });
    await page.getByRole("radio", { name: "디카페인만" }).click();
    await page.getByRole("button", { name: "다음" }).click();
    await page.getByRole("button", { name: "과일" }).click();
    await page.getByRole("button", { name: "다음" }).click();
    await page.getByRole("button", { name: "시작하기" }).click({ timeout: 120_000 });
    await page.waitForURL((u) => u.pathname === "/", { timeout: 60_000 });
    if (ANALYZE) {
      await page.getByRole("tab", { name: "개인 카페" }).click();
      await page.getByPlaceholder(/예: /).fill(ANALYZE);
      await page.getByRole("button", { name: "분석", exact: true }).click();
    } else {
      await page.getByRole("button", { name: BRAND }).click({ timeout: 120_000 });
    }
    const cards = page.locator("article");
    await cards.first().waitFor({ timeout: 120_000 });
    for (let i = 0; i < 120 && (await page.getByText("설명을 쓰는 중…").count()) > 0; i++) await sleep(500);
    await sleep(800);
    await cards.first().screenshot({ path: OUT });
    await page.screenshot({ path: PAGE_OUT, fullPage: true });
    console.log(`wrote ${OUT}\nwrote ${PAGE_OUT}`);
    for (let i = 0; i < (await cards.count()); i++) console.log(`--- card ${i + 1}\n${await cards.nth(i).innerText()}`);
  } finally {
    await browser.close();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
