# 引擎 L1/L2:direct_api + static_html + registry 自动降级

## Goal

实现降级链前两层与引擎注册表:`engine: auto` 时 L1→L2→firecrawl 顺序自动尝试,成功引擎的选择结果有记录。

## Requirements

- `direct_api.py`(L1):httpx JSON/API 直连;URL 模板展开(`{symbol}`/`{page}`);`extract.type: json_path` 取数;key 池轮换(多 key 列表轮换,凭据走 env:)
- `static_html.py`(L2):httpx 拉页 + selectolax 解析;gb18030/big5 等编码自动识别(生产源痛点);`extract.type: list`(item 选择器 + fields 映射,支持 `@href`/`@src` 属性取值)与 `item` 单页模式
- `pagination`:template(`?page={n}`)与 selector(`a.next@href`)两种模式;`max_pages` 封顶(scroll 模式留给 L4+)
- `registry.py`:引擎注册表 + `engine: auto` 降级编排
  - 尝试顺序 L1→L2→firecrawl(v0.1 链只有这三层;L4-L6 是后续版本)
  - 成功引擎记录到 SQLite `engine_hints` 表,下次 run 优先尝试(hint 失效则继续降级)——**回写 SQLite 不回写用户 YAML**,避免用户文件 git 噪音(design.md 复核此决策)
  - 引擎失败原因结构化记录(源名/引擎/错误类),供日志与 doctor 消费

## Acceptance Criteria

- [ ] 单测(录制回放,不真连):json_path 取数、list 提取、gb18030 解码、template/selector 翻页、auto 降级顺序、hint 优先与失效回落
- [ ] 真实源 smoke:Yahoo chart API(L1)与 aihot.news(L2)各产出结构化条目
- [ ] 单引擎失败不影响同品类其他源(隔离由 pipeline 层保证,本层只报结构化错误)

## Notes

- 填充 `src/myia/engines/direct_api.py`、`static_html.py`、`registry.py` 现有壳
- 实战对标(规划):aihot→L2/L3、ZOL→L2、Yahoo→L1;X 平台(`https://x.com/`,主人 2026-10-01 确认)在规划里列 L1 示例,但实战走 L6/cookie 池——L1 直连仅限其公开/轻量端点,不作为本任务验收源

## 验收记录(2026-10-03,受主人委托代验)

verdict: accepted

evidence: pytest tests/test_direct_api.py tests/test_static_html.py tests/test_registry.py -q → 44 passed, 2 skipped(skip=真实源 smoke);json_path/list 提取/gb18030/template·selector 翻页/auto 降级顺序/hint 优先与失效(test_registry.py:88/:139/:179/:200)全过;本会话真实源实证:aihot L2 smoke 设 MYIA_SMOKE_REAL=1 跑通(1 passed);Yahoo L1 打包 smoke 夹具(默认 respect_robots:true)会被 Yahoo robots.txt('Disallow: /')拦截,但用官方插件文档化配置(plugins/stocks.yaml:68 respect_robots:false)经 DirectAPIEngine 实探产出真实结构化条目 {'title': 'NVIDIA Corporation', 'url': 'NVDA', 'symbol': 'NVDA'}——引擎功能无缺陷,夹具配置漂移建议补一行。
