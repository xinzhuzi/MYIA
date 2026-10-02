# 引擎 firecrawl:重渲染源后端

## Goal

对接 firecrawl 作为 JS 重渲染源的后端,进入 registry 降级链第三层(对应规划六层链的 L3 替代后端)。

## Requirements

- 对接 `POST /v1/scrape`:端点可配(自建默认 `http://127.0.0.1:3002`,云 API 备选);endpoint 与 API key 走 `env:` 引用,禁明文
- 返回 markdown / html,供 `extract` 节解析或作为 LLM 文本(v0.1 先支持 extract 解析路径)
- 作为 firecrawl 引擎注册进 registry,参与 `engine: auto` 降级(L1→L2→firecrawl)
- 超时与失败结构化上报(自建服务挂掉时降级链正常收尾,不挂死流水线)
- 配置:`sources[].engine_options.firecrawl`(formats/timeout 等),缺省值合理

## Acceptance Criteria

- [ ] 单测用本地 mock server:请求体、鉴权头、markdown 解析、超时失败路径
- [ ] 真实自建端点 smoke 测试标注 skip-by-default(本地有服务才跑,CI 不依赖)
- [ ] 降级链单测:L2 失败自动落到 firecrawl 成功

## Notes

- 填充 `src/myia/engines/firecrawl.py` 壳(26 行)
- 生产参照:私有自建 firecrawl 抓 aihot.news(本地迁移包 `configs/firecrawl/`、`docs/Firecrawl-部署与使用手册.md`,仅本地)
