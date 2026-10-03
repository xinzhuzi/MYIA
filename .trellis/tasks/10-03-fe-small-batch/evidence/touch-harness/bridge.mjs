/**
 * 触屏交互存证桥:vite(5173)页面 ←→ 真 sidecar(desktop/entry.py serve,stdio)。
 *
 * - POST /rpc {method, params} → sidecar JSON-RPC 往返;应答 result → 200;
 *   应答 error → 500,body = 错误对象 JSON 文本(前端 toSidecarError 按字符串
 *   拒绝值解析,见 client.ts:97-119,形状不变)。
 * - GET /events → SSE;sidecar stdout 无 id 行(type 事件)原样转发。
 * - GET /health → version 往返成功后 200(就绪探针)。
 * - FORCE_DRY=1(默认开):run.start params.dry 强制 true —— 存证零外网;
 *   run 子进程/管线/日志环/run 注册表全真,仅采集推送走 dry(入档说明)。
 *
 * 用法:node bridge.mjs <sandbox_home> [port=8797]
 */
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "../../../../..");
const sandbox = resolve(process.argv[2] ?? "");
const port = Number(process.argv[3] ?? 8797);
if (!sandbox || !existsSync(resolve(sandbox, "myia.db"))) {
  console.error(`[bridge] sandbox 未造数(缺 myia.db): ${sandbox}`);
  process.exit(2);
}

const FORCE_DRY = process.env.FORCE_DRY !== "0";

const child = spawn("uv", ["run", "python", "desktop/entry.py", "serve"], {
  cwd: repoRoot,
  env: { ...process.env, MYIA_HOME: sandbox },
  stdio: ["pipe", "pipe", "pipe"],
});

let nextId = 1;
const pending = new Map();
const sseClients = new Set();
let ready = false;

const send = (obj) => child.stdin.write(JSON.stringify(obj) + "\n");

child.stdout.setEncoding("utf8");
let buf = "";
child.stdout.on("data", (chunk) => {
  buf += chunk;
  let idx;
  while ((idx = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (!line) continue;
    let obj;
    try {
      obj = JSON.parse(line);
    } catch {
      console.error(`[bridge] 非 JSON 行: ${line.slice(0, 120)}`);
      continue;
    }
    if ("id" in obj && ("result" in obj || "error" in obj)) {
      const waiter = pending.get(obj.id);
      if (waiter) {
        pending.delete(obj.id);
        waiter(obj);
      }
    } else {
      const frame = `data: ${JSON.stringify(obj)}\n\n`;
      for (const res of sseClients) res.write(frame);
    }
  }
});
child.stderr.setEncoding("utf8");
child.stderr.on("data", (chunk) => process.stderr.write(`[sidecar] ${chunk}`));
child.on("exit", (code) => {
  console.error(`[bridge] sidecar 退出 code=${code}`);
  process.exit(code === 0 ? 0 : 1);
});

const request = (method, params, timeoutMs = 60000) =>
  new Promise((resolveP, rejectP) => {
    const id = nextId++;
    const timer = setTimeout(() => {
      pending.delete(id);
      rejectP(new Error(`rpc 超时: ${method}`));
    }, timeoutMs);
    pending.set(id, (obj) => {
      clearTimeout(timer);
      resolveP(obj);
    });
    const finalParams = { ...params };
    if (FORCE_DRY && method === "run.start") {
      finalParams.dry = true;
    }
    send({ id, method, params: finalParams });
  });

const server = createServer((req, res) => {
  const cors = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
  };
  if (req.method === "OPTIONS") {
    res.writeHead(204, cors);
    res.end();
    return;
  }
  if (req.method === "GET" && req.url === "/events") {
    res.writeHead(200, { ...cors, "Content-Type": "text/event-stream", Connection: "keep-alive" });
    res.write(":open\n\n");
    sseClients.add(res);
    req.on("close", () => sseClients.delete(res));
    return;
  }
  if (req.method === "GET" && req.url === "/health") {
    if (ready) {
      res.writeHead(200, cors);
      res.end('{"ok":true}');
    } else {
      res.writeHead(503, cors);
      res.end('{"ok":false}');
    }
    return;
  }
  if (req.method === "POST" && req.url === "/rpc") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      let parsed;
      try {
        parsed = JSON.parse(body);
      } catch {
        res.writeHead(400, cors);
        res.end('{"code":"invalid_params","path":"$","message":"bridge 收到非 JSON"}');
        return;
      }
      request(parsed.method, parsed.params ?? {})
        .then((obj) => {
          if ("error" in obj) {
            res.writeHead(500, { ...cors, "Content-Type": "application/json" });
            res.end(JSON.stringify(obj.error));
          } else {
            res.writeHead(200, { ...cors, "Content-Type": "application/json" });
            res.end(JSON.stringify(obj.result ?? null));
          }
        })
        .catch((err) => {
          res.writeHead(504, { ...cors, "Content-Type": "application/json" });
          res.end(JSON.stringify({ code: "bridge_timeout", path: "$", message: String(err) }));
        });
    });
    return;
  }
  res.writeHead(404, cors);
  res.end("not found");
});

server.listen(port, "127.0.0.1", () => {
  console.error(`[bridge] http://127.0.0.1:${port} sandbox=${sandbox} FORCE_DRY=${FORCE_DRY}`);
  request("version", {})
    .then((obj) => {
      ready = true;
      console.error(`[bridge] sidecar version 往返 OK: ${JSON.stringify(obj.result)}`);
    })
    .catch((err) => console.error(`[bridge] version 往返失败: ${err}`));
});

const shutdown = () => {
  server.close();
  child.kill("SIGTERM");
};
process.on("SIGINT", () => {
  shutdown();
  process.exit(0);
});
process.on("SIGTERM", () => {
  shutdown();
  process.exit(0);
});
