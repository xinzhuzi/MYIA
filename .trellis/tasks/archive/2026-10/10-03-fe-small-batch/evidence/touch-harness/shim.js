/**
 * 浏览器内 Tauri IPC shim(Playwright addInitScript 注入,先于应用加载)。
 *
 * 浏览器直开 vite(无 Tauri 壳)时,@tauri-apps/api 的 invoke/listen 走
 * window.__TAURI_INTERNALS__(core.js:328 / event.js:139)。本 shim 把:
 * - invoke("sidecar_request") → 桥 POST /rpc(拒绝值 = 错误对象 JSON 字符串,
 *   与 Rust Err(String) 同形状,client.ts toSidecarError 解析路径不变);
 * - invoke("plugin:event|listen") → 注册回调 + 订阅桥 SSE,sidecar 事件行
 *   以 {event:"sidecar://event", payload:<行>} 派发(壳侧 main.rs 同语义)。
 */
(() => {
  const BRIDGE = "http://127.0.0.1:8797";
  let cbId = 0;
  const callbacks = new Map();
  const listeners = {}; // eventName -> handlerId[]

  const ensureSSE = () => {
    if (window.__myiaSSE) return;
    const es = new EventSource(BRIDGE + "/events");
    es.onmessage = (message) => {
      let payload;
      try {
        payload = JSON.parse(message.data);
      } catch {
        return;
      }
      for (const handlerId of listeners["sidecar://event"] ?? []) {
        const entry = callbacks.get(handlerId);
        if (entry) {
          entry.cb({ event: "sidecar://event", id: 0, payload });
          if (entry.once) callbacks.delete(handlerId);
        }
      }
    };
    window.__myiaSSE = es;
  };

  window.__TAURI_INTERNALS__ = {
    transformCallback(callback, once = false) {
      const id = ++cbId;
      callbacks.set(id, { cb: callback, once });
      return id;
    },
    unregisterCallback(id) {
      callbacks.delete(id);
    },
    async invoke(cmd, args = {}, options) {
      if (cmd === "plugin:event|listen") {
        (listeners[args.event] ?? (listeners[args.event] = [])).push(args.handler);
        ensureSSE();
        return Date.now();
      }
      if (cmd === "plugin:event|unlisten") {
        return undefined;
      }
      if (cmd !== "sidecar_request") {
        return undefined;
      }
      const response = await fetch(BRIDGE + "/rpc", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(args),
      });
      if (!response.ok) {
        throw await response.text();
      }
      return await response.json();
    },
  };
})();
