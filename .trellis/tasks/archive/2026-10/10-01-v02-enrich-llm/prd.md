# LLM 精评:批量 / 缓存 / 预算护栏 + watchlist 画像

## Goal

第二层漏斗实装(v1.7 分析侧补全的核心):LLM 三维打分 + 成本护栏 + 相关性画像。桌面用户挂自己的 key,没有护栏会烧穿。

## Requirements

- enrich 节实装(v01-yaml-schema 已定义):enabled / model(任何 OpenAI 兼容端点,**缺省 glm-4-flash**)/ base_url(**必须显式配置、env: 引用——MYIA 无内置端点、无默认 key**,grill Q6 定案)/ scores(value, relevance, credibility 0-10)/ batch / cache / budget_per_run
- 打分:批量精评(默认 20 条/请求);relevance 以 `watchlist.keywords` 为基准,`mute` 词命中即降权/归档
- 缓存:同 URL 结果缓存(enrich_cache 表),schema 变更或手动失效才重评
- 预算护栏:单轮 token 预算(budget_per_run,如 50000);超预算本轮自动降级纯粗筛并在日志/doctor 提示(规划定案)
- prompt 模板外置(数据文件),反馈闭环(v0.3)可调
- score 回填 items 表,push.route 依据真实 score 分层;无 score 缺省行为与 v01-push 对齐

## Acceptance Criteria

- [ ] mock LLM 单测:打分解析/容错、缓存命中不重评、预算耗尽触发降级、mute 降权
- [ ] 缺 base_url、或 base_url 未用凭据引用时结构化报错(grill Q6)
- [ ] 真实 GLM key 手动验证一次(结果记任务日志,凭据不进仓库)
- [ ] 端到端:同一插件开/关 enrich,route 分层行为符合预期

## Notes

- 填充 `src/myia/enrich/`(25 行壳);analyze 步骤接口位已在 v01-pipeline 留好

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: uv run --no-sync python -m pytest tests/test_enrich.py -q → 50 passed;mock 单测四件套齐:test_parse_payload_*(打分解析/容错)、test_enrich_cache_hit_does_not_rescore(缓存命中不重评)、test_enrich_budget_exhaust_degrades_to_keyword_only(预算耗尽降级)、test_enrich_mute_hit_demotes_without_llm(mute 降权);base_url 结构化报错=test_settings_missing_base_url_raises_missing_base_url + test_settings_plaintext_base_url_raises_credential_plaintext(grill Q6);端到端开关分层=test_enrich_on_routes_by_score_three_tiers + test_enrich_off_falls_back_to_v01_conservative_default;缺省 glm-4-flash=src/myia/schema.py:191;prompt 外置=src/myia/enrich/data/prompt.json。

遗留(需主人手动完成):
- AC『真实 GLM key 手动验证一次(结果记任务日志)』未做:任务目录与 .trellis/workspace 均无验证记录,需主人持真实凭据验证,凭据不进仓库。
