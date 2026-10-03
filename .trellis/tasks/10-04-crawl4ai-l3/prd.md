# PRD:crawl4ai L3 收实——L2 零结果降级探测 + 真网 JS 源冒烟 + 文档补全

## 背景

主人 2026-10-03 选定「crawl4ai L3 实装」开工(做软件方向,v1.2 池)。**建档探查反转(2026-10-04 本会话实读)**:该池档旧口径「L3 薄层实质缺位」已过时——并行线归档任务 `archive/2026-10/10-03-v12-crawl4ai-l3` 已完成实装并验收(commit 8e8d2ab/60022ff/dee06cd;真库接入 `crawl4ai.py:114-139/211-298`、惰性 `shishi[crawl4ai]` extras、CI 零外网假模块回放测试 955 行 34 例、基线 1822 全绿;本机 .venv 已装 crawl4ai 0.9.4+playwright 1.63)。上游核实:unclecode/crawl4ai 84.7k★ Apache-2.0(2026-10-03 gh api),markdown 图片输出标准 `![alt](绝对URL)`(vendored html2text `ignore_images=False`,urljoin 绝对化)与看图线 `_MARKDOWN_IMAGE_RE`(collect.py:171)兼容。

**真剩余面(本任务)= 三件**:

### 1. L2 零结果不降级 L3(产品缺陷,核心)

- 现状:auto 链 L2 static_html 提取 0 条视为「成功+健康空页」,并**回写 hint 锁死 L2**(registry.py:228-229;static_html.py:113-115;pipeline.py:1556-1563)——JS 渲染空壳页(正需要 L3 的页面)在 auto 模式下**永远到不了 L3**;只有显式 `engine: crawl4ai` 或 L2 真报错才触发 L3
- 目标:零结果触发一次**有界 L3 探测**——L3 也零则维持空页语义(不误报),L3 出条则采用并 hint 改 L3;防浏览器开销滥用(探测只对零结果页,且受页预算/单 run 探测上限约束)
- 指纹 skip(变更指纹未变)不触发探测——那是真·无更新,不是 JS 壳

### 2. 真网 JS 源冒烟(从未实跑的诚实边界)

- 归档档 prd.md:91-93 明言真实 JS 源 smoke 需 `MYIA_SMOKE_REAL=1` 手动、从未跑过;本任务补:挑 1-2 个真实 JS 渲染页(候选:aihot.news 文章页——本会话已证其为 JS-SPA、curl 静态拿不到图)真跑 L3 全链,断言条目/markdown/同域图收集;顺带验证看图线 JS 页路径(L3 源 → markdown 图 → OCR)
- 证据进 evidence/,失败如实(网络/反爬),不造假

### 3. 文档补全

- `docs/zh/schema.md`(及 en 对应件)engine_options 节补 crawl4ai 键(timeout/headless/browser_options/run_options 透传,自管键清单)——现只写了 firecrawl/scrapling/stealth_browser(schema.md:97-99)

## Requirements

1. 零结果 L3 探测:降级链语义改动集中在 registry/pipeline,守三条底线——指纹 skip 不探测、探测有单 run 上限(拍板①)、L3 探测失败/零条回退原空页语义零误报;测试覆盖:JS 壳页经探测被 L3 接住、真空页探测后仍空、指纹 skip 不探测、探测预算耗尽、hint 回写正确
2. 真网冒烟:`MYIA_SMOKE_REAL=1` 跑通至少一个真实 JS 页(条目+markdown 断言;有图则图收集断言);8080/vision 链路顺带验证(可选)
3. schema 文档:zh/en 双语补齐 crawl4ai 键,示例对齐现码(engine_options 声明面)

## Acceptance Criteria

1. fixture:JS 壳页(假 crawl4ai 回放)在 auto 模式下 L2 零条 → L3 探测 → 条目落地且 hint=L3;第二跑直接走 L3
2. fixture:真空页 L3 探测零条 → 结果仍为空页语义,run 健康,无重复浏览器开销(hint 不变)
3. 指纹 skip 页零探测(断言不构造浏览器)
4. 单 run 探测上限生效(超限源零结果按空页收,不阻塞)
5. 真网:≥1 个真实 JS 页 L3 全链真跑出条目(证据在档;网络失败如实标注并留重试口径)
6. docs/zh+en schema.md crawl4ai 键齐,`test_docs` 类文档一致性测试(若有)绿
7. 基线不回归:pytest/vitest(不动 UI 则免)/tsc 以开工基线为准;CI 同款命令

## 待拍板(grill 项)

1. **零结果探测策略**:A 无条件对每源零结果首跑探测(简单,推荐+单 run 上限兜底)vs B HTML 特征启发式(含 `<div id="root">`/超短正文/已知框架签名才探测,省浏览器但漏判风险)
2. **单 run 探测上限**:推荐 3 源/run(拍板数)
3. **L3 探测的页预算**:独立短帽(推荐 30s)vs 沿用引擎 60s
4. **冒烟对象**:aihot 文章页(现成 SPA 实证,推荐)是否再加一个(如 linux.do 登录墙外的公开页?反爬未知,不强求)
