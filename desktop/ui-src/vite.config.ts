import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// MYIA 桌面前端构建配置。
// - 产物输出到 desktop/ui/(tauri.conf.json build.frontendDist 指向处,不动);
//   outDir 在本目录之外,vite 默认不清空,必须显式 emptyOutDir 清掉旧产物。
// - beforeBuildCommand(desktop/src-tauri/tauri.conf.json)即 `npm run build`。
// - vitest(`npm test`)与构建共用本配置:jsdom 环境跑 5 屏组件测试
//   (screens/*/*.test.tsx 头部另有 @vitest-environment jsdom 标注,双保险)。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "../ui",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    strictPort: true,
  },
  test: {
    environment: "jsdom",
  },
  // Tauri IPC 与打包环境变量前缀;clearScreen 关闭以免冲掉 sidecar 的终端日志。
  envPrefix: ["VITE_", "TAURI_"],
  clearScreen: false,
});
