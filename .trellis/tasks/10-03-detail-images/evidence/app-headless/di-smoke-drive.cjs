// /tmp/myia-di-e2e/di-smoke-drive.cjs — 10-03-detail-images AC8 无头 GUI 回归
// 法 = yaml-editor/vision-pipeline 无头法复用:vite dev(5221)+ bridge-di.mjs
// (sidecar=当前源码 desktop/entry.py serve)+ Playwright headless chromium +
// __TAURI_INTERNALS__ shim。唯一 shim 注入:store.items 补 params.db = fixture 库
// (runs/main/myia.db,含 detail 链 image_ocr 条目)——真壳 db 由 MYIA_HOME 决定。
// 旧断言回归:①侧栏无「看图」②#/image 重定向;新断言:③feed「图」Badge +
// 图析行渲染(image_ocr 经 detail 链落地)。
const { chromium } = require("/Users/zhengbingjin/.npm/_npx/705bc6b22212b352/node_modules/playwright");
const fs = require("fs");
const path = require("path");

const BASE = "http://127.0.0.1:5221";
const BRIDGE = "http://127.0.0.1:54118";
const FIXTURE_DB = "/tmp/myia-di-e2e/runs/main/myia.db";
const OUT = "/Users/zhengbingjin/Project/Github/MYIA/.trellis/tasks/10-03-detail-images/evidence/app-headless";

const steps = [];
const consoleErrors = [];
function record(name, ok, evidence, screenshot) {
  steps.push({ name, ok, evidence, screenshot });
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name} :: ${evidence.slice(0, 500)}`);
}

const SHIM = `
(() => {
  const BRIDGE = ${JSON.stringify(BRIDGE)};
  const FIXTURE_DB = ${JSON.stringify(FIXTURE_DB)};
  let cbSeq = 0;
  const invoke = async (cmd, args = {}, options) => {
    if (cmd === "sidecar_request") {
      const method = args && args.method;
      const params = Object.assign({}, (args && args.params) || {});
      if (method === "store.items" && !params.db) params.db = FIXTURE_DB; // 唯一注入(见文件头)
      let res;
      try {
        res = await fetch(BRIDGE + "/rpc", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ method, params }),
        });
      } catch (e) {
        throw JSON.stringify({ code: "transport_error", path: "$", message: "smoke bridge 不可达: " + e });
      }
      if (res.status === 503) throw JSON.stringify({ code: "sidecar_not_running", path: "$", message: "bridge 503" });
      if (!res.ok) throw JSON.stringify({ code: "transport_error", path: "$", message: "bridge HTTP " + res.status });
      const msg = await res.json();
      if (msg && msg.error) throw JSON.stringify(msg.error);
      return msg.result;
    }
    if (cmd === "plugin:event|listen" || cmd === "plugin:event|unlisten") return 0;
    if (cmd === "plugin:app|version") return "0.0.1-smoke";
    throw JSON.stringify({ code: "unsupported_command", path: "$", message: "smoke shim 未实现命令: " + cmd });
  };
  window.__TAURI_INTERNALS__ = {
    invoke,
    transformCallback: (cb, once) => ++cbSeq,
    unregisterCallback: () => {},
    metadata: { currentWindow: { label: "main" }, currentWebview: { label: "main" } },
  };
})();
`;

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 300)); });
  page.on("pageerror", (e) => consoleErrors.push("pageerror: " + String(e).slice(0, 300)));
  await page.addInitScript(SHIM);

  // ---- 1. 侧栏无「看图」(旧断言) ----
  await page.goto(`${BASE}/#/feed`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  const nav = await page.$$eval("nav a, aside a, [aria-label] a", (as) =>
    as.map((a) => (a.textContent || "").replace(/\s+/g, " ").trim()).filter(Boolean));
  const navAll = nav.length ? nav : await page.$$eval("a", (as) => as.map((a) => (a.textContent || "").trim()).filter(Boolean));
  const hasKantu = navAll.some((t) => t.includes("看图"));
  await page.screenshot({ path: path.join(OUT, "ac8-01-sidebar.png"), fullPage: true });
  record(
    "AC8:侧栏无「看图」项(拆屏后旧断言)",
    !hasKantu && navAll.length >= 5,
    `导航项 ${navAll.length} 个:${JSON.stringify(navAll)};含看图=${hasKantu}`,
    "ac8-01-sidebar.png",
  );

  // ---- 2. #/image 重定向(旧断言) ----
  await page.goto(`${BASE}/#/image`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);
  const hash = await page.evaluate(() => location.hash);
  const bodyText = (await page.evaluate(() => document.body.innerText)).slice(0, 200);
  await page.screenshot({ path: path.join(OUT, "ac8-02-image-route.png"), fullPage: true });
  record(
    "AC8:#/image 路由 → 重定向(404 兜底 Navigate to /)",
    hash === "#/" || hash === "",
    `goto #/image 后 location.hash=${JSON.stringify(hash)};正文首 100 字=${JSON.stringify(bodyText.slice(0, 100))}`,
    "ac8-02-image-route.png",
  );

  // ---- 3. feed「图」Badge + 图析行渲染(detail 链 image_ocr) ----
  await page.goto(`${BASE}/#/feed`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  const feed = await page.evaluate(() => {
    const badge = [...document.querySelectorAll('span,badge,[class*="badge"]')].map((b) => (b.textContent || "").trim());
    const tu = badge.filter((t) => t === "图");
    const tuRow = [...document.querySelectorAll('[title]')].map((s) => s.getAttribute('title') || '').filter((t) => t.indexOf('MYIA DETAIL') >= 0 || t.indexOf('\u56fe\u6790') >= 0);
    return { bodyHasOcrText: document.body.innerText.includes("MYIA DETAIL IMAGES E2E"), badgeTuCount: tu.length, tuRowSample: tuRow.slice(0, 2) };
  });
  await page.screenshot({ path: path.join(OUT, "ac8-03-feed-image-row.png"), fullPage: true });
  record(
    "AC8:feed「图」Badge + 图析行(detail 链 image_ocr 渲染)",
    feed.badgeTuCount >= 1 && feed.bodyHasOcrText,
    `「图」Badge ${feed.badgeTuCount} 个(条目 a);OCR 文本渲染=${feed.bodyHasOcrText};图析行样例=${JSON.stringify(feed.tuRowSample)}`,
    "ac8-03-feed-image-row.png",
  );

  fs.writeFileSync(path.join(OUT, "ac8-headless-transcript.json"), JSON.stringify({ steps, consoleErrors: consoleErrors.slice(0, 20) }, null, 2));
  console.log("\n==== SUMMARY ====");
  for (const s of steps) console.log(`${s.ok ? "PASS" : "FAIL"}  ${s.name}`);
  await browser.close();
  process.exit(steps.every((s) => s.ok) ? 0 : 2);
})().catch((e) => {
  console.error("DRIVER CRASH:", e);
  fs.writeFileSync(path.join(OUT, "ac8-headless-transcript.json"), JSON.stringify({ steps, crash: String(e), consoleErrors }, null, 2));
  process.exit(1);
});
