// install-verify-drive.cjs — 10-03-detail-images AC7 装机无头 GUI 验证(Playwright 法)
// 法 = di-smoke-drive.cjs 同构:vite 构建产物(desktop/ui,即 tauri frontendDist
// 嵌进 .app 的那份)静态 serve + bridge-install.mjs(sidecar=**已装 app 的冻结
// myia-core**)+ Playwright headless chromium + __TAURI_INTERNALS__ shim。
// 唯一注入:store.items 补 params.db = fixture 库(含 detail 链 image_ocr 条目)。
// 断言:①侧栏无「看图」②feed「图」Badge+图析行渲染 ③settings 看图分区在。
const { chromium } = require("/Users/zhengbingjin/.npm/_npx/705bc6b22212b352/node_modules/playwright");
const fs = require("fs");
const path = require("path");
const http = require("node:http");

const UI_DIR = "/Users/zhengbingjin/Project/Github/MYIA/desktop/ui";
const BASE = "http://127.0.0.1:5223";
const BRIDGE = "http://127.0.0.1:54130";
const FIXTURE_DB = "/tmp/myia-di-e2e/runs/main/myia.db";
const OUT = "/Users/zhengbingjin/Project/Github/MYIA/.trellis/tasks/10-03-detail-images/evidence/install";

const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json" };
const uiServer = http.createServer((req, res) => {
  let p = req.url.split("?")[0];
  if (p === "/") p = "/index.html";
  const file = path.join(UI_DIR, p);
  if (!file.startsWith(UI_DIR) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404); res.end("not found"); return;
  }
  res.writeHead(200, { "content-type": MIME[path.extname(file)] || "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
});

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
        throw JSON.stringify({ code: "transport_error", path: "$", message: "install bridge 不可达: " + e });
      }
      if (res.status === 503) throw JSON.stringify({ code: "sidecar_not_running", path: "$", message: "bridge 503" });
      if (!res.ok) throw JSON.stringify({ code: "transport_error", path: "$", message: "bridge HTTP " + res.status });
      const msg = await res.json();
      if (msg && msg.error) throw JSON.stringify(msg.error);
      return msg.result;
    }
    if (cmd === "plugin:event|listen" || cmd === "plugin:event|unlisten") return 0;
    if (cmd === "plugin:app|version") return "0.0.1-install-verify";
    throw JSON.stringify({ code: "unsupported_command", path: "$", message: "install shim 未实现命令: " + cmd });
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
  await new Promise((r) => uiServer.listen(5223, "127.0.0.1", r));
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 300)); });
  page.on("pageerror", (e) => consoleErrors.push("pageerror: " + String(e).slice(0, 300)));
  await page.addInitScript(SHIM);

  // ---- 1. 侧栏无「看图」 ----
  await page.goto(`${BASE}/#/feed`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  const nav = await page.$$eval("nav a, aside a, [aria-label] a", (as) =>
    as.map((a) => (a.textContent || "").replace(/\s+/g, " ").trim()).filter(Boolean));
  const navAll = nav.length ? nav : await page.$$eval("a", (as) => as.map((a) => (a.textContent || "").trim()).filter(Boolean));
  const hasKantu = navAll.some((t) => t.includes("看图"));
  await page.screenshot({ path: path.join(OUT, "ac7-01-sidebar.png"), fullPage: true });
  record(
    "AC7:侧栏无「看图」项",
    !hasKantu && navAll.length >= 5,
    `导航项 ${navAll.length} 个:${JSON.stringify(navAll)};含看图=${hasKantu}`,
    "ac7-01-sidebar.png",
  );

  // ---- 2. feed「图」Badge + 图析行渲染(detail 链 image_ocr) ----
  await page.goto(`${BASE}/#/feed`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  const feed = await page.evaluate(() => {
    const badge = [...document.querySelectorAll('span,badge,[class*="badge"]')].map((b) => (b.textContent || "").trim());
    const tu = badge.filter((t) => t === "图");
    const tuRow = [...document.querySelectorAll('[title]')].map((s) => s.getAttribute('title') || '').filter((t) => t.indexOf('MYIA DETAIL') >= 0 || t.indexOf('\u56fe\u6790') >= 0);
    return { bodyHasOcrText: document.body.innerText.includes("MYIA DETAIL IMAGES E2E"), badgeTuCount: tu.length, tuRowSample: tuRow.slice(0, 2) };
  });
  await page.screenshot({ path: path.join(OUT, "ac7-02-feed-image-row.png"), fullPage: true });
  record(
    "AC7:feed「图」Badge + 图析行渲染",
    feed.badgeTuCount >= 1 && feed.bodyHasOcrText,
    `「图」Badge ${feed.badgeTuCount} 个;OCR 文本渲染=${feed.bodyHasOcrText};图析行样例=${JSON.stringify(feed.tuRowSample)}`,
    "ac7-02-feed-image-row.png",
  );

  // ---- 3. settings 看图分区在(vision-form CardTitle「看图」+「二级看图通道」) ----
  await page.goto(`${BASE}/#/settings`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3000);
  const settings = await page.evaluate(() => {
    const titles = [...document.querySelectorAll("h1,h2,h3,[class*='card-title'],[data-slot='card-title']")].map((t) => (t.textContent || "").replace(/\s+/g, " ").trim());
    const body = document.body.innerText;
    return {
      hasKantuTitle: titles.some((t) => t.includes("看图")),
      hasChannelDesc: body.includes("二级看图通道"),
      sampleTitles: titles.slice(0, 12),
    };
  });
  await page.screenshot({ path: path.join(OUT, "ac7-03-settings-vision.png"), fullPage: true });
  record(
    "AC7:settings 看图分区在(VisionForm 卡片)",
    settings.hasKantuTitle && settings.hasChannelDesc,
    `看图卡片标题=${settings.hasKantuTitle};「二级看图通道」描述=${settings.hasChannelDesc};卡片标题样例=${JSON.stringify(settings.sampleTitles)}`,
    "ac7-03-settings-vision.png",
  );

  fs.writeFileSync(path.join(OUT, "ac7-install-verify-transcript.json"), JSON.stringify({ steps, consoleErrors: consoleErrors.slice(0, 20) }, null, 2));
  console.log("\n==== SUMMARY ====");
  for (const s of steps) console.log(`${s.ok ? "PASS" : "FAIL"}  ${s.name}`);
  await browser.close();
  uiServer.close();
  process.exit(steps.every((s) => s.ok) ? 0 : 2);
})().catch((e) => {
  console.error("DRIVER CRASH:", e);
  fs.writeFileSync(path.join(OUT, "ac7-install-verify-transcript.json"), JSON.stringify({ steps, crash: String(e), consoleErrors }, null, 2));
  process.exit(1);
});
