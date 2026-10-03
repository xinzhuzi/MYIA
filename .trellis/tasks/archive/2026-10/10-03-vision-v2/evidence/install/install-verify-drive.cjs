// install-verify-drive.cjs — 10-03-vision-v2 装机无头 GUI 验证(Playwright 法)
// 法 = 10-03-detail-images evidence/install/install-verify-drive.cjs 同构:
// vite 构建产物(desktop/ui,即 tauri frontendDist 嵌进 .app 的那份)静态 serve
// + bridge-install-v2.mjs(sidecar=**已装 app 的冻结 myia-core**,MYIA_HOME=真实
// 数据根)+ Playwright headless chromium + __TAURI_INTERNALS__ shim。
// 唯一注入:store.items 补 params.db = v2 fixture 库(④ 展开断言的逐行置信度
// /caption 数据;真实库暂无 image 条目)。②③ 用真数据(真模型目录/真 8080)。
// 断言:①侧栏无「看图」②settings 看图模型管理卡 + qwen3-vl-8b-mlx 真数据
// ③vision-server-badge 服务徽章 ④feed 图析展开(OCR 全文+逐行置信度+caption)。
const { chromium } = require("/Users/zhengbingjin/.npm/_npx/705bc6b22212b352/node_modules/playwright");
const fs = require("fs");
const path = require("path");
const http = require("node:http");

const UI_DIR = "/Users/zhengbingjin/Project/Github/MYIA/desktop/ui";
const BASE = "http://127.0.0.1:5224";
const BRIDGE = "http://127.0.0.1:54131";
const FIXTURE_DB = "/tmp/myia-v2-install/fixture-v2.db";
const OUT = "/Users/zhengbingjin/Project/Github/MYIA/.trellis/tasks/10-03-vision-v2/evidence/install";

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
  await new Promise((r) => uiServer.listen(5224, "127.0.0.1", r));
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 300)); });
  page.on("pageerror", (e) => consoleErrors.push("pageerror: " + String(e).slice(0, 300)));
  await page.addInitScript(SHIM);

  // ---- ① 侧栏无「看图」 ----
  await page.goto(`${BASE}/#/feed`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  const nav = await page.$$eval("nav a, aside a, [aria-label] a", (as) =>
    as.map((a) => (a.textContent || "").replace(/\s+/g, " ").trim()).filter(Boolean));
  const navAll = nav.length ? nav : await page.$$eval("a", (as) => as.map((a) => (a.textContent || "").trim()).filter(Boolean));
  const hasKantu = navAll.some((t) => t.includes("看图"));
  await page.screenshot({ path: path.join(OUT, "v2-01-sidebar.png"), fullPage: true });
  record(
    "①侧栏无「看图」项",
    !hasKantu && navAll.length >= 5,
    `导航项 ${navAll.length} 个:${JSON.stringify(navAll)};含看图=${hasKantu}`,
    "v2-01-sidebar.png",
  );

  // ---- ② settings「看图模型管理」卡 + qwen3-vl-8b-mlx 真数据 ----
  // settings 已分区化(左列分区导航,?section= 驱动,10-03-ui-deep-imitation):
  // 看图卡在 vision 分区,深链 #/settings?section=vision(settings-screen.tsx:375)。
  await page.goto(`${BASE}/#/settings?section=vision`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3000);
  const card = page.locator('[data-testid="vision-models-card"]');
  const cardVisible = await card.count().then((n) => n === 1 && card.isVisible());
  const modelRow = page.locator('[data-testid="vision-model-qwen3-vl-8b-mlx"]');
  let modelInfo = null;
  if (await modelRow.count()) {
    modelInfo = {
      text: (await modelRow.innerText()).replace(/\s+/g, " ").trim(),
      active: (await modelRow.locator("text=当前").count()) > 0,
      path: await modelRow.locator("span[title]").first().getAttribute("title"),
    };
  }
  const settingsTitles = await page.$$eval("h1,h2,h3,[data-slot='card-title'],[class*='card-title']", (ts) =>
    ts.map((t) => (t.textContent || "").replace(/\s+/g, " ").trim()).filter(Boolean));
  await page.screenshot({ path: path.join(OUT, "v2-02-settings-models.png"), fullPage: true });
  record(
    "②settings 看图模型管理卡渲染且清单含 qwen3-vl-8b-mlx(真数据)",
    cardVisible && !!modelInfo && modelInfo.text.includes("qwen3-vl-8b-mlx") && modelInfo.active,
    `卡片在=${cardVisible};标题「看图模型管理」=${settingsTitles.includes("看图模型管理")};模型行=${JSON.stringify(modelInfo)}`,
    "v2-02-settings-models.png",
  );

  // ---- ③ 服务徽章(vision-server-badge 真状态) ----
  const badge = page.locator('[data-testid="vision-server-badge"]');
  let badgeText = null;
  if (await badge.count()) badgeText = (await badge.innerText()).trim();
  try { await badge.first().screenshot({ path: path.join(OUT, "v2-03-server-badge.png") }); } catch {}
  record(
    "③服务徽章渲染(真 8080 状态)",
    badgeText !== null && badgeText !== "" && badgeText !== "探测中…" && badgeText !== "确保启动中…",
    `徽章文本=${JSON.stringify(badgeText)}(image.server.status 探真实 127.0.0.1:8080)`,
    "v2-03-server-badge.png",
  );

  // ---- ④ feed 图析展开(fixture 库:OCR 全文 + 逐行置信度 + caption) ----
  await page.goto(`${BASE}/#/feed`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  const card1 = page.locator('[data-testid="feed-item-1"]');
  const expandBtn = card1.locator('button[aria-label="展开条目"]');
  const collapsedOcrRow = await page.locator('[data-testid="feed-image-ocr-1"]').count();
  let detail = { clicked: false, linesBlock: false, lineTexts: [], captionBlock: false, captionText: null, ocrFullBlock: false };
  if (await expandBtn.count()) {
    await expandBtn.click();
    await page.waitForTimeout(600);
    detail.clicked = true;
    detail.ocrFullBlock = (await page.locator('[data-testid="feed-image-ocr-1"]').innerText()).includes("OCR 全文");
    const lines = page.locator('[data-testid="feed-image-ocr-lines-1"]');
    detail.linesBlock = (await lines.count()) === 1;
    detail.lineTexts = await lines.locator("div.flex span.text-xs").allInnerTexts();
    detail.confs = await lines.locator("span.font-mono").allInnerTexts();
    const caption = page.locator('[data-testid="feed-image-caption-1"]');
    detail.captionBlock = (await caption.count()) === 1;
    if (detail.captionBlock) detail.captionText = (await caption.innerText()).replace(/\s+/g, " ").trim();
  }
  await card1.screenshot({ path: path.join(OUT, "v2-04-feed-expanded.png") }).catch(() => {});
  await page.screenshot({ path: path.join(OUT, "v2-04-feed-expanded-full.png"), fullPage: true });
  const linesOk = detail.linesBlock && detail.lineTexts.length === 3 && (detail.confs || []).includes("98%") && (detail.confs || []).includes("41%");
  record(
    "④feed 图析展开(OCR 全文 + 逐行置信度 + caption)",
    collapsedOcrRow >= 1 && detail.clicked && detail.ocrFullBlock && linesOk && detail.captionBlock,
    `收起态图析行=${collapsedOcrRow >= 1};展开点击=${detail.clicked};OCR 全文块=${detail.ocrFullBlock};逐行=${detail.lineTexts.length} 行 conf=${JSON.stringify(detail.confs)};caption=${detail.captionText}`,
    "v2-04-feed-expanded.png",
  );

  fs.writeFileSync(path.join(OUT, "v2-install-verify-transcript.json"), JSON.stringify({ steps, consoleErrors: consoleErrors.slice(0, 20) }, null, 2));
  console.log("\n==== SUMMARY ====");
  for (const s of steps) console.log(`${s.ok ? "PASS" : "FAIL"}  ${s.name}`);
  await browser.close();
  uiServer.close();
  process.exit(steps.every((s) => s.ok) ? 0 : 2);
})().catch((e) => {
  console.error("DRIVER CRASH:", e);
  fs.writeFileSync(path.join(OUT, "v2-install-verify-transcript.json"), JSON.stringify({ steps, crash: String(e), consoleErrors }, null, 2));
  process.exit(1);
});
