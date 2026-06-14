/**
 * build-walkthrough.mjs — turn a before/ + after/ screenshot pair into a
 * shippable URL-by-URL side-by-side walkthrough (HTML + composite PNGs +
 * one tall render you can drop into chat).
 *
 * Layout convention: OUT dir contains `before/` and `after/` subdirs of
 * `<id>-<profile>.png` files (produce them with capture.mjs). This writes
 * `walkthrough.html`, `composites/cmp-<id>.png`, and `walkthrough-full.png`
 * into OUT.
 *
 * Run from the project root (needs the project's Playwright for the render):
 *   OUT=/tmp/shots MANIFEST=/tmp/shots/manifest.json \
 *   node ~/.claude/skills/mf-frontend-design/scripts/build-walkthrough.mjs
 *
 * Manifest (JSON):
 * {
 *   "title": "Studio goes mobile", "subtitle": "one-line lede",
 *   "accent": "#ff6b2b", "profile": "mobile",
 *   "meta": ["Branch X", "8 files changed", "0 overflow"],
 *   "surfaces": [
 *     { "id":"chat", "url":"/chat", "name":"Chat home", "verdict":"fixed",
 *       "beforeCap":"…", "afterCap":"…",
 *       "changes":["Rail no longer steals 56px", "All tiles fit"] }
 *   ],
 *   "highlights": [ { "img":"after/_hl-nav-drawer.png", "cap":"Slide-in nav drawer" } ],
 *   "desktop": [ { "img":"after/studio-desktop.png", "cap":"/studio desktop intact" } ]
 * }
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { pathToFileURL } from "node:url";

const OUT = process.env.OUT;
const MANIFEST = process.env.MANIFEST;
if (!OUT || !MANIFEST) { console.error("OUT and MANIFEST env are required"); process.exit(1); }
const m = JSON.parse(readFileSync(MANIFEST, "utf8"));
const accent = m.accent || "#ff6b2b";
const profile = m.profile || "mobile";
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const img = (src, cls = "") => `<figure class="${cls}"><img src="${esc(src)}" alt=""><figcaption>__CAP__</figcaption></figure>`;
function pair(s) {
  const b = `before/${s.id}-${profile}.png`, a = `after/${s.id}-${profile}.png`;
  return `
  <section class="surface">
    <div class="surf-head">
      <span class="url">${esc(s.url || s.id)}</span><span class="surf-name">${esc(s.name || "")}</span>
      <span class="verdict v-${esc(s.verdict || "fixed")}">${esc(s.verdict || "fixed")}</span>
    </div>
    <div class="pair">
      <figure><img src="${b}" alt=""><figcaption><i class="tag before"></i>${esc(s.beforeCap || "Before")}</figcaption></figure>
      <figure><img src="${a}" alt=""><figcaption><i class="tag after"></i>${esc(s.afterCap || "After")}</figcaption></figure>
    </div>
    ${(s.changes || []).length ? `<ul class="changes">${s.changes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
  </section>`;
}

const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(m.title || "Before / After Walkthrough")}</title>
<style>
:root{--bg:#0b0b0d;--panel:#141417;--line:#26262b;--ink:#ededf0;--mut:#9a9aa3;--accent:${accent};--good:#46c17e;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:0 24px 120px}
header.hero{padding:72px 24px 36px;max-width:1180px;margin:0 auto}
.eyebrow{font:600 11px/1 ui-monospace,Menlo,monospace;letter-spacing:.22em;text-transform:uppercase;color:var(--accent)}
h1{font-size:clamp(34px,5vw,56px);line-height:1.02;letter-spacing:-.03em;margin:16px 0 14px;font-weight:700}
h1 .dot{color:var(--accent)}.lede{color:var(--mut);font-size:18px;max-width:70ch}
.meta{display:flex;flex-wrap:wrap;gap:10px;margin-top:26px}
.chip{border:1px solid var(--line);border-radius:999px;padding:7px 14px;font-size:13px;color:var(--mut)}.chip b{color:var(--ink);font-weight:600}
section.surface{padding-top:60px}.surf-head{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.url{font:600 22px/1 ui-monospace,Menlo,monospace;color:var(--ink)}.surf-name{color:var(--mut);font-size:15px}
.verdict{margin-left:auto;font:600 12px/1 ui-monospace,monospace;color:var(--good);letter-spacing:.04em;display:inline-flex;gap:7px;align-items:center}
.verdict::before{content:"";width:8px;height:8px;border-radius:999px;background:var(--good)}.v-broken::before,.v-todo::before{background:var(--accent)}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:22px}
@media(max-width:720px){.pair{grid-template-columns:1fr}}
figure{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:16px;overflow:hidden}
figure img{display:block;width:100%;height:auto}
figcaption{display:flex;align-items:center;gap:9px;padding:11px 15px;font:600 11px/1 ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase;border-top:1px solid var(--line);color:var(--mut)}
.tag{width:9px;height:9px;border-radius:3px}.tag.before{background:#6b6b73}.tag.after{background:var(--accent)}
.changes{margin:22px 0 0;padding:0;list-style:none;columns:2;column-gap:34px}
@media(max-width:720px){.changes{columns:1}}
.changes li{break-inside:avoid;padding:8px 0 8px 26px;position:relative;font-size:15px}
.changes li::before{content:"\\2192";position:absolute;left:0;top:7px;color:var(--accent);font-weight:700}
.hl-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-top:22px}
@media(max-width:920px){.hl-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:520px){.hl-grid{grid-template-columns:1fr}}
.hl figcaption{text-transform:none;letter-spacing:0;font-weight:500;font-size:12px}
h2.block{font-size:13px;letter-spacing:.2em;text-transform:uppercase;color:var(--mut);font-family:ui-monospace,monospace;margin:0;padding-top:8px}
.note{background:var(--panel);border:1px solid var(--line);border-left:2px solid var(--accent);border-radius:12px;padding:16px 20px;color:var(--mut);font-size:14.5px;margin-top:26px}.note b{color:var(--ink)}
footer{margin-top:72px;padding-top:28px;border-top:1px solid var(--line);color:var(--mut);font-size:14px}
code{font:13px ui-monospace,monospace;background:#1c1c20;padding:2px 7px;border-radius:6px}
</style></head><body>
<header class="hero">
  <div class="eyebrow">${esc(m.eyebrow || "before / after")}</div>
  <h1>${esc(m.title || "Before / After")}<span class="dot">.</span></h1>
  ${m.subtitle ? `<p class="lede">${esc(m.subtitle)}</p>` : ""}
  ${(m.meta || []).length ? `<div class="meta">${m.meta.map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div>` : ""}
</header>
<div class="wrap">
  ${m.note ? `<div class="note">${esc(m.note)}</div>` : ""}
  ${(m.surfaces || []).map(pair).join("\n")}
  ${(m.highlights || []).length ? `<section class="surface"><h2 class="block">Highlights</h2><div class="hl-grid">${m.highlights.map((h) => `<figure class="hl"><img src="${esc(h.img)}" alt=""><figcaption>${esc(h.cap)}</figcaption></figure>`).join("")}</div></section>` : ""}
  ${(m.desktop || []).length ? `<section class="surface"><h2 class="block">Desktop — no regression</h2><div class="pair">${m.desktop.map((d) => `<figure><img src="${esc(d.img)}" alt=""><figcaption><i class="tag after"></i>${esc(d.cap)}</figcaption></figure>`).join("")}</div></section>` : ""}
  ${m.footer ? `<footer>${m.footer}</footer>` : ""}
</div></body></html>`;

writeFileSync(path.join(OUT, "walkthrough.html"), html);
console.log("wrote walkthrough.html");

// Composite PNGs (best-effort; needs ImageMagick `montage`).
let font = "";
for (const f of ["/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf"]) {
  try { execFileSync("test", ["-f", f]); font = f; break; } catch {}
}
try {
  mkdirSync(path.join(OUT, "composites"), { recursive: true });
  for (const s of m.surfaces || []) {
    const args = [];
    if (font) args.push("-font", font);
    args.push("-label", "BEFORE", path.join(OUT, `before/${s.id}-${profile}.png`),
              "-label", "AFTER",  path.join(OUT, `after/${s.id}-${profile}.png`),
              "-tile", "2x1", "-geometry", "x820+16+10",
              "-background", "#faf9f7", "-bordercolor", "#e5e4e2", "-border", "1",
              "-fill", "#18181b", "-pointsize", "26",
              "-title", `${s.url || s.id}  —  ${profile}`,
              path.join(OUT, `composites/cmp-${s.id}.png`));
    execFileSync("montage", args);
  }
  console.log("wrote composites/cmp-*.png");
} catch (e) { console.log("composites skipped (ImageMagick montage not available):", e.message.split("\n")[0]); }

// Tall full-page render of the HTML (so it can be dropped into chat).
try {
  const PW = process.env.PW || path.join(process.cwd(), "node_modules/playwright/index.js");
  const { chromium } = await import(pathToFileURL(PW).href).then((x) => x.default ?? x);
  const b = await chromium.launch({ headless: true });
  const ctx = await b.newContext({ viewport: { width: 1180, height: 1000 }, deviceScaleFactor: 1 });
  const p = await ctx.newPage();
  await p.goto(pathToFileURL(path.join(OUT, "walkthrough.html")).href, { waitUntil: "networkidle" });
  const broken = await p.evaluate(() => Array.from(document.images).filter((i) => !i.complete || i.naturalWidth === 0).map((i) => i.getAttribute("src")));
  if (broken.length) console.log("WARN broken images:", JSON.stringify(broken));
  await p.screenshot({ path: path.join(OUT, "walkthrough-full.png"), fullPage: true });
  await b.close();
  console.log("wrote walkthrough-full.png");
} catch (e) { console.log("full render skipped:", e.message.split("\n")[0]); }

console.log("DONE ->", path.join(OUT, "walkthrough.html"));
