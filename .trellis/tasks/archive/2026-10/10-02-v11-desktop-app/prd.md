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

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: 功能件全有仓库内证据:dmg 产物在库(desktop/src-tauri/target/release/bundle/dmg/MYIA_0.1.0_aarch64.dmg,22,998,331 字节,门禁 10-03 08:14 构建);五屏组件测试我实跑 desktop/ui-src `npm test`→7 files/45 tests 全绿;源管理写回 sidecar 已实现(desktop/entry.py:670 _m_sources_write,注册 :1850)+ 往返测试 tests/test_desktop_sidecar_protocol.py:522(该文件 46 tests 我实跑绿);updater 接入(tauri.conf.json:42-46 endpoints+pubkey 占位、desktop/UPDATER.md 完整 runbook、settings/updater-card.test.tsx);CI desktop-release.yml macos-dmg 主线 + windows msi 构建级 continue-on-error(PRD 明文允许);实机冒烟记录 .trellis/workspace/xinzhuzi/journal-1.md:46(tauri build 重装 /Applications + OCR 目视五屏数据渲染)。

遗留(需主人手动完成):
- updater 签名校验+静默升级演示一次:需主人生成签名密钥并发布测试更新源(UPDATER.md runbook 已备;grill R2-1 定「密钥=主人侧前置」)
- dmg「下载即用」与冷启动 <1s:需主人真机实测;现有记录为 OCR 冒烟目视,无 <1s 计时数据
- CI 产出 dmg/msi 附件:需主人推 tag 触发 desktop-release.yml;Windows 实机交互按 PRD 标注需主人
- 桌面 e2e(启动→跑插件→情报流出卡)无独立自动化脚本:现有为 journal 脚本化冒烟记录(journal-1.md:46-47),完全自动化属后续工程
- tauri.conf.json 已是 1.1.1 但门禁产出的 dmg 文件名仍 0.1.0(旧构建产物;版本对齐归 10-03-v111-release 任务)
- 本批变更待主人提交
