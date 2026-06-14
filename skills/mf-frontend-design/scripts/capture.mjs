/**
 * capture.mjs — robust, repeatable screenshot capture for before/after passes.
 *
 * Drives the TARGET PROJECT's own Playwright (so the browser version matches),
 * freezes animations, pauses <video>, hides the Next.js dev indicator, and
 * shoots each surface at one or more viewport profiles. Built for the
 * mf-frontend-design "screenshot-driven verification" + "before/after
 * walkthrough" workflow.
 *
 * Run it from the project root (so Playwright resolves), e.g.:
 *   BASE=http://localhost:3000 OUT=/tmp/shots/after \
 *   SURFACES="home:/,pricing:/pricing,app:/dashboard" \
 *   node ~/.claude/skills/mf-frontend-design/scripts/capture.mjs
 *
 * Env:
 *   BASE      base URL                       (default http://localhost:3000)
 *   OUT       output dir (created)           (required)
 *   SURFACES  "id:path,id:path"              (default "home:/")
 *   PROF      "mobile,desktop" subset        (default both)
 *   PW        path to playwright entry       (default <cwd>/node_modules/playwright/index.js)
 *   FULL      "1" to also shoot full-page mobile scroll
 */
import path from "node:path";
import { pathToFileURL } from "node:url";

const BASE = process.env.BASE || "http://localhost:3000";
const OUT = process.env.OUT;
if (!OUT) { console.error("OUT env (output dir) is required"); process.exit(1); }
const PW = process.env.PW || path.join(process.cwd(), "node_modules/playwright/index.js");
const { chromium } = await import(pathToFileURL(PW).href).then((m) => m.default ?? m);

const SURFACES = (process.env.SURFACES || "home:/")
  .split(",").map((s) => s.trim()).filter(Boolean)
  .map((pair) => { const i = pair.indexOf(":"); return { id: pair.slice(0, i), path: pair.slice(i + 1) }; });

let PROFILES = [
  { tag: "mobile",  w: 390,  h: 844, dsf: 2, mobile: true,
    ua: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1" },
  { tag: "desktop", w: 1440, h: 900, dsf: 1, mobile: false },
];
if (process.env.PROF) {
  const tags = process.env.PROF.split(",").map((s) => s.trim());
  PROFILES = PROFILES.filter((p) => tags.includes(p.tag));
}

const FREEZE = `
*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important;}
nextjs-portal,#__next-build-watcher,[data-nextjs-toast]{display:none!important;}
`;

async function settle(page) {
  await page.addStyleTag({ content: FREEZE }).catch(() => {});
  await page.evaluate(() => {
    document.querySelectorAll("video").forEach((v) => { try { v.pause(); v.loop = false; } catch {} });
    // Neuter rAF so perpetual JS animations stop scheduling and the
    // compositor goes idle (heavy framer-motion / canvas apps otherwise
    // never reach a stable frame and the screenshot times out).
    window.requestAnimationFrame = () => 0;
  }).catch(() => {});
  await page.waitForTimeout(800);
}

async function shoot(browser, surface, profile) {
  const ctx = await browser.newContext({
    viewport: { width: profile.w, height: profile.h },
    deviceScaleFactor: profile.dsf, isMobile: profile.mobile, hasTouch: profile.mobile,
    reducedMotion: "reduce", userAgent: profile.ua,
  });
  const page = await ctx.newPage();
  try {
    await page.goto(BASE + surface.path, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
    await settle(page);
    await page.screenshot({ path: `${OUT}/${surface.id}-${profile.tag}.png`, fullPage: false, timeout: 30000 });
    if (profile.mobile && process.env.FULL === "1") {
      await page.screenshot({ path: `${OUT}/${surface.id}-${profile.tag}-full.png`, fullPage: true, timeout: 30000 });
    }
    console.log(`OK   ${surface.id} ${profile.tag}  -> ${page.url()}`);
  } catch (e) {
    console.log(`FAIL ${surface.id} ${profile.tag}  ${e.message.split("\n")[0]}`);
  } finally {
    await ctx.close();
  }
}

import { mkdirSync } from "node:fs";
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ headless: true });
for (const s of SURFACES) for (const p of PROFILES) await shoot(browser, s, p);
await browser.close();
console.log("DONE ->", OUT);
