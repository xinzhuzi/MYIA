# Skill sources

本目录下是前端 UI 对口技能(2026-10-03 建,任务 `10-03-ui-deep-imitation` 配套资产)。仅本地知识层安装,未运行任何上游安装脚本。

| Skill | 来源 | 说明 |
|---|---|---|
| `frontend-design` | [anthropics/skills](https://github.com/anthropics/skills) `skills/frontend-design` | Anthropic 官方;独特视觉方向/字体排印/反模板化审美;npx 直装 10-03 |
| `frontend-ui-engineering` | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) `skills/frontend-ui-engineering` | 生产级 UI 工程(组件架构/WCAG/响应式/反 AI 审美);93.7K★ MIT;npx 直装 10-03 |
| `web-design-reviewer` | [github/awesome-copilot](https://github.com/github/awesome-copilot) `skills/web-design-reviewer` | 渲染态视觉审查:响应式/可访问性/视觉一致性/布局破损,定位到源码修;github.com 克隆通道当日抽风,**自另一私有仓库镜像拷贝**(真实指针见 LOCAL-NOTES.md;该镜像 10-03 与上游逐字节核验一致) |
| `react-vite-best-practices` | [AsyrafHussin/agent-skills](https://github.com/AsyrafHussin/agent-skills) `skills/react-vite-best-practices` | React+Vite 性能 23 规则(代码分割/懒加载/包体);本项目 ui-src 即 Vite;npx 直装 10-03 |
| `vercel-react-best-practices` | [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) `skills/react-best-practices`(上游目录无 vercel- 前缀,本地名沿另一私有仓库惯例) | Vercel 官方 React 性能 70 规则;npx 当日静默失败,**自该私有仓库镜像拷贝**(10-03 与上游逐字节同) |
| `vercel-composition-patterns` | 同上 `skills/composition-patterns` | 组件组合模式(布尔 prop 蔓延/复合组件/React 19 API);同上镜像拷贝 |
| `tauri` | [ericrisco/rsc-harness](https://github.com/ericrisco/rsc-harness) `skills/tauri` | Rust 壳层对口:Tauri v2 工程(IPC 命令/capabilities ACL/updater 签名/sidecar/构建分发,含 references+verify.sh+evals);141★ 但内容为原创工程经验,三方评估(含 mtga 中文版/Microck 镜像)中最佳;npx 直装 10-03;**自带 verify.sh 已对本仓 src-tauri 实跑通过** |

## 安装与更新方式

- 正路:`npx -y skills add <owner/repo@skill> -y`(真源落 `.agents/skills/`,自动向 `.claude/skills/` 铺副本;`.claude/` 整体已 gitignore)。
- github.com 克隆不通时的兜底:自另一私有仓库(真实指针见 LOCAL-NOTES.md)的 `.agents/skills/` 镜像拷贝(该仓有逐字节核验台账),拷后按 LOCAL-NOTES.md 红线词表验无私有词残留。
- 更新:重跑 `npx skills add` 同 spec 覆盖,或对照该私有仓库台账手动同步;改动日期记入本表。

## 刻意未装(2026-10-03 裁定)

- `typescript-react-reviewer` — 曾装后当日裁定移除:评审角度与 vercel-react-best-practices(70 规则)+ trellis-check 独立质检流程重叠,本仓评审走 trellis 不走独立 reviewer 技能。
- `webapp-testing` — 曾装后当日裁定移除:与本仓已立的无头 GUI 冒烟法(Playwright+bridge.mjs+Tauri shim,见 yaml-editor 任务 evidence)及用户级 playwright 技能重叠。
- `electron` — 本项目桌面壳是 Tauri,不对口。
- **Rust 相关评估后未装(2026-10-03,主人令「rust 相关也需要安装」后三轮调查)**:①wshobson/agents `systems-programming`(39.6k★)——Tokio 服务端/C-C++ 向,本仓 Rust 面=283 行 Tauri 胶水(main.rs)不对口;**后备**:若 Rust 侧长出重并发逻辑,单独摘其 `rust-async-patterns`(12.6KB 纯 Rust,质量上乘);②BiFangKNT/mtga `.codex/skills/tauri`(1.1k★ 仓)——中文 v2 编排式但浅且缺 updater,已被 rsc-harness 件覆盖;③Microck/ordinary-claude-skills `tauri`——爬虫拼贴+references 断链+v1 API 混入(emit_all/v1 签名结构),弃;④majiayu000/rainoftime 系 rust-borrow-checker 单 topic 件——低质注册表,不装。
- 另一私有仓库的域件(comfyui/krea/minimax-h3/remotion-*/lyric-*/musical-*/qwen-*)— 另一私有应用专用(真实名与路径见 LOCAL-NOTES.md),与情报桌面端无关。
- `tdd` / `security-review` / `lsp-*` — 本仓已有 trellis-check 测试纪律、security-baseline 红线、ZCode 内置 LSP,重复资产不装。
- superpowers 全家(若遇)— 用户已令全网清除,不得装入。
