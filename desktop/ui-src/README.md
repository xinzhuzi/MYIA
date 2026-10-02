# MYIA 桌面前端(ui-src)

Vite + React 19 + TypeScript + Tailwind 4 + shadcn/ui 惯例组件的桌面客户端前端。
**构建产物输出到 `../ui/`** —— `desktop/src-tauri/tauri.conf.json` 的
`build.frontendDist` 指向处(不动),`beforeBuildCommand` 即 `npm run build`。

## 命令

```bash
npm install          # 本目录独立 package.json,不碰根 pyproject/uv.lock
npm run dev          # vite dev(http://localhost:5173;tauri dev 经 build.devUrl 对接)
npm run build        # tsc -b && vite build → ../ui/
npm run preview      # 本地预览构建产物(无 Tauri IPC,顶栏显示「未在 Tauri 环境中」)
```

桌面壳联调:

```bash
cd ..                # desktop/
npx tauri dev        # 需先在本目录起 npm run dev(devUrl 已指向 5173)
npx tauri build      # beforeBuildCommand 自动执行 ui-src 的 npm run build
```

## 结构

```
src/
├── lib/api/         sidecar 协议 TS 全量封装(权威定义:../entry.py 模块注释)
│   ├── types.ts     方法↔参数/结果映射、流式事件、结构化错误(逐字段对照 Python 侧)
│   ├── client.ts    invoke("sidecar_request") 门面 api.* + onSidecarEvent 事件订阅
├── hooks/           use-sidecar-status(顶栏全局状态:C 阶段可并入 run 活动事件)
├── components/
│   ├── layout/      app-layout / sidebar(五屏导航)/ top-bar(品类+状态)/ page-header
│   ├── ui/          shadcn 惯例组件(button/card/badge/select/separator/skeleton)
│   ├── myia-mark.tsx  品牌标(事实源:../branding/myia-icon.svg → public/ 拷贝)
│   └── empty-state.tsx 统一空态(品牌图标+渐变光晕+中文文案)
├── routes/          五屏:dashboard / feed / sources / logs / settings(骨架空路由)
├── App.tsx          路由表(HashRouter;/ = 仪表盘)
└── index.css        Linear 暗色质感 token(CSS 变量 + Tailwind 4 @theme)
```

## 边界(2026-10-02 v1.1 UI 骨架)

- 本目录只做**骨架**:布局/导航/设计 token/协议 client;五屏业务逻辑
  (列表渲染、表格交互、run 触发、表单写回)由 C 阶段任务实现。
- 协议以 `desktop/entry.py` 为权威;client 的类型是它的逐字段镜像,
  协议升级时先改 Python 侧再同步本目录。
- 构建产物 `../ui/` 由 `emptyOutDir` 全量重建,不要手改其中文件。
