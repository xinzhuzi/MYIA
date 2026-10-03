# MYIA 项目规范索引

> 配置驱动的开源情报中枢。第一原则 AI-NATIVE:第一使用者是 AI,schema 完备度与结构化输出是硬要求。

| 规范 | 内容 |
|------|------|
| [python/index.md](./python/index.md) | 工程约定:uv / pydantic / 异步 / 目录结构 |
| [python/error-handling.md](./python/error-handling.md) | 结构化错误、退出码、fail-fast |
| [python/logging.md](./python/logging.md) | 结构化日志与 skip 原因 |
| [python/quality.md](./python/quality.md) | 测试约定(录制回放/夹具脱敏)、CI |
| [domain/yaml-schema.md](./domain/yaml-schema.md) | 品类 YAML schema 硬规则 |
| [domain/security-baseline.md](./domain/security-baseline.md) | 凭据与安全底线(铁律) |
| [domain/connector-selection.md](./domain/connector-selection.md) | 云端连接器选型门禁:免费路径硬规则 |
| [domain/os-etiquette.md](./domain/os-etiquette.md) | 系统交互礼仪:静默操作铁律(禁抢焦点/清场禁全目录还原/共享构建缓存隔离) |
| [desktop/sidecar-protocol.md](./desktop/sidecar-protocol.md) | 桌面 sidecar 协议:方法注册表(23)与错误码(事实源 = entry.py `_HANDLERS`) |
| [desktop/frontend-ui.md](./desktop/frontend-ui.md) | 桌面前端 UI:token 体系(色彩/字号/动效/elevation/焦点)+ 改 UI 必读检查单(事实源 = `desktop/ui-src/src/index.css`) |
| [guides/](./guides/index.md) | 通用思维指南(复用/跨层) |
| [guides/ai-dispatch-template.md](./guides/ai-dispatch-template.md) | AI 任务分发模板(v1.1 协议与顺序) |
| [guides/engineering-discipline.md](./guides/engineering-discipline.md) | 工程纪律:长任务监控 / 先报量再动手(查改分家) / 高星参考 |
| [guides/search-sop.md](./guides/search-sop.md) | 搜索 SOP:热路径/工具分工/仓库外路径/网络路由/GitNexus(先读后搜) |

权威产品规划:`LOCAL-NOTES.md 索引的规划文档(本地)`(仅本地,勿提交)。
