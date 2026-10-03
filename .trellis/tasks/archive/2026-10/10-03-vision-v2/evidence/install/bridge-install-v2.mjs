// bridge-install-v2.mjs — 10-03-vision-v2 装机验证桥(法同 10-03-detail-images
// evidence/install/bridge-install.mjs):spawn **已装 app 的冻结二进制**
// /Applications/世事.app/Contents/MacOS/myia-core serve——验证的就是装机产物本身。
// 与先例唯一差异:MYIA_HOME 指向**真实数据根**(~/Library/Application Support/MYIA),
// 使 image.models.list 扫真模型目录(② 真数据断言)、image.server.status 探真
// 8080(③ 服务徽章真状态);全链只发读方法(models.list/status/config.read/
// store.items),零写入零 ensure(桥层面不碰主人进程)。
// 协议:stdin 一行 {"id","method","params"} → stdout 一行 {"id","result"|"error"}。
import { createServer } from "node:http";
import { spawn } from "node:child_process";

const SIDECAR = "/Applications/世事.app/Contents/MacOS/myia-core";
const PORT = Number(process.argv[2] || 0) || 54131;
const HOME = `${process.env.HOME}/Library/Application Support/MYIA`;

const child = spawn(SIDECAR, ["serve"], {
  cwd: "/",
  stdio: ["pipe", "pipe", "pipe"],
  env: { ...process.env, MYIA_HOME: HOME },
});

let buffer = "";
const pending = new Map();
let nextId = 1;

child.stdout.setEncoding("utf8");
child.stdout.on("data", (chunk) => {
  buffer += chunk;
  let idx;
  while ((idx = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, idx).trim();
    buffer = buffer.slice(idx + 1);
    if (!line) continue;
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      continue;
    }
    if (msg && msg.id !== undefined && pending.has(msg.id)) {
      const waiter = pending.get(msg.id);
      pending.delete(msg.id);
      waiter(msg);
    }
  }
});
child.stderr.setEncoding("utf8");
child.stderr.on("data", (c) => process.stderr.write(`[sidecar] ${c}`));
child.on("exit", (code, signal) => {
  console.error(`[bridge] sidecar exited code=${code} signal=${signal}`);
  for (const waiter of pending.values()) waiter({ error: { code: "sidecar_not_running", path: "$", message: `sidecar 进程未运行 (exit ${code}/${signal})` } });
  pending.clear();
  sidecarAlive = false;
});

let sidecarAlive = true;

function rpc(method, params) {
  return new Promise((resolve) => {
    if (!sidecarAlive || child.stdin.destroyed) {
      resolve({ error: { code: "sidecar_not_running", path: "$", message: "sidecar 进程未运行" } });
      return;
    }
    const id = nextId++;
    const timer = setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        resolve({ error: { code: "sidecar_timeout", path: "$", message: "sidecar 应答超时(120s)" } });
      }
    }, 120_000);
    pending.set(id, (msg) => {
      clearTimeout(timer);
      resolve(msg);
    });
    child.stdin.write(JSON.stringify({ id, method, params: params ?? {} }) + "\n");
  });
}

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "POST, GET, OPTIONS",
  "access-control-allow-headers": "content-type",
};

const server = createServer((req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, CORS);
    res.end();
    return;
  }
  if (req.method === "POST" && req.url === "/rpc") {
    let body = "";
    req.setEncoding("utf8");
    req.on("data", (c) => (body += c));
    req.on("end", async () => {
      let payload;
      try {
        payload = JSON.parse(body || "{}");
      } catch {
        res.writeHead(400, CORS);
        res.end(JSON.stringify({ error: { code: "bad_request", path: "$", message: "invalid json" } }));
        return;
      }
      const msg = await rpc(payload.method, payload.params);
      res.writeHead(200, { ...CORS, "content-type": "application/json" });
      res.end(JSON.stringify(msg));
    });
    return;
  }
  res.writeHead(404, CORS);
  res.end("{}");
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`bridge on http://127.0.0.1:${PORT} (sidecar: ${SIDECAR}, MYIA_HOME: ${HOME})`);
});

const shutdown = () => {
  try { child.stdin.end(); child.kill(); } catch {}
  server.close();
  process.exit(0);
};
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
