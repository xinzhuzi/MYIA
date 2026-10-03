// bridge-di.mjs — detail-images AC8 无头冒烟桥(拷自 .zcode/smoke/bridge.mjs,
// 唯一差异:sidecar 不用冻结二进制,改用**当前源码** desktop/entry.py serve
// (detail-images 改动只在源码树;冻结镜像早于本任务)。协议同构:stdin 一行
// {"id","method","params"} → stdout 一行 {"id","result"|,"error"}。
import { createServer } from "node:http";
import { spawn } from "node:child_process";

const REPO = "/Users/zhengbingjin/Project/Github/MYIA";
const PY = `${REPO}/desktop/.venv-build/bin/python`;
const PORT = Number(process.argv[2] || 0) || 54118;

const child = spawn(PY, [`${REPO}/desktop/entry.py`, "serve"], {
  cwd: REPO,
  stdio: ["pipe", "pipe", "pipe"],
  env: { ...process.env, MYIA_HOME: "" }, // dev 回退(同 yaml-editor 桥语义)
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
  console.log(`bridge on http://127.0.0.1:${PORT}`);
});

const shutdown = () => {
  try { child.stdin.end(); child.kill(); } catch {}
  server.close();
  process.exit(0);
};
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
