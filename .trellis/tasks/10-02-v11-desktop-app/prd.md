# v1.1 桌面正式版:五大界面 + 打包流水线

## Goal

把 desktop/ 的 spike 地基(Tauri 2 + PyInstaller sidecar,实测 25MiB/144ms/72 行胶水)升级为产品级桌面客户端——grill Q11 定案的 v1.1 头号交付。

## 范围(依据规划〇.5「界面构成」)

五大界面(React 19 + TS + Tailwind 4 + shadcn/ui,骨架照抄 tiangolo/full-stack-fastapi-template 的裁剪惯例):
1. **仪表盘**:品类状态/源健康度(ok/degraded/dead)/采集量趋势
2. **情报流**:卡片瀑布(未读/星标/稍后读,Miniflux 式信息架构)
3. **源管理**:TanStack 表格(排序/筛选/分页/列宽)+ 增删改查写回品类 YAML
4. **采集日志**:实时终端(run 瀑布日志、耗时、错误高亮,Crawlab 式)
5. **设置**:LLM key(入钥匙链)/代理池/推送通道/反馈开关

系统件:
- **updater**:Tauri updater 官方模式(静默+签名校验,规划模仿表 #16)
- **打包流水线**:desktop/ 出 dmg(Mac)/msi(Windows),CI 产物 attaches;sidecar 构建脚本产品化(现 build-sidecar.sh)
- sidecar 协议升级:spike 的单命令往返 → 结构化 JSON-RPC/流式(日志/进度推送),错误结构化透传

## 约束

- 铁律不变:凭据只进钥匙链(YAML/界面输入皆然);桌面零 Docker;plugin 装不上不拦核心
- 数据面复用:SQLite 单库 + 现有 store/CLI 语义,不另起后端服务(sidecar 即后端)
- 视觉锚点:Linear 暗色质感 + Vercel 数据密度(规划定稿);品牌图标 branding/
- scope 纪律:五界面为 v1.1 全量;Web 面板、多语言 UI 等延后

## Acceptance Criteria

- [ ] dmg 下载即用(无 Docker/Python 前置),冷启动 <1s,五界面全部可用
- [ ] 源管理界面改动能写回品类 YAML 并被 `myia run` 识别(往返一致)
- [ ] updater:签名校验 + 静默升级演示一次(测试更新源)
- [ ] CI 产出 dmg/msi 附件;Windows 至少完成构建级验证(实机交互可标注需主人)
- [ ] 全量 pytest 绿;桌面 e2e(启动→跑插件→情报流出卡)自动化或脚本化记录

## Notes

- 建议实现顺序:sidecar 协议升级 → 界面骨架+导航 → 源管理(最有用)→ 仪表盘/日志 → 设置 → updater → 流水线
- 本任务与 v11-plugins-source-arch 并行友好(插件层在 Python 侧)
