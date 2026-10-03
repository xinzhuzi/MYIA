# YAML schema:12 节定义 + 加载校验

## Goal

品类 YAML 的数据模型与加载校验层——「AI 写 YAML」的地基。schema 完备度 = 产品完备度,每一节都要有明确语义与缺省值。

## Requirements

- 覆盖规划 v1.7 第三节 12 节(权威示例见规划 `plugins/stocks.yaml` 完整 schema 段,仅本地):
  1. `id` / `name`
  2. `schedule`(cron)+ `timezone`(缺省跟系统)
  3. `sources[]`:`engine`(auto|direct_api|static_html|crawl4ai|scrapling|stealth_browser|llm_browser)、`url`(支持 `{symbol}`/`{page}` 模板)、`method`(GET/POST + `post_body`)、`headers`(凭据禁明文)、`pagination`(template|selector|scroll + `max_pages`)、`extract`(type: list|item|json_path + item + fields)、`rate_limit`(qps/jitter/backoff/respect_robots)、`proxy`、`retry`、源级扩展参数(如 symbols)
  4. `watchlist`(keywords / mute)
  5. `classify.rules[]`(name / when / tag)
  6. `dedup.key`(组合键模板)
  7. `enrich`(enabled / model / scores / batch / cache / budget_per_run)
  8. `push[]`(channel / target / route[] / template)
  9. `storage`(retention / vacuum)
- 每节缺省值明确;未知顶层字段 fail-fast 报错(AI 生成质量问题要在加载期可检出)
- 凭据检查:headers 值不含 `env:` / `keychain:` 前缀的疑似凭据键(Cookie/Authorization/Token)→ LoadError 拒跑
- v0.1 只实现 `env:` 解析;`keychain:` 值明确报错并提示 v0.2 支持

## Acceptance Criteria

- [ ] 规划第三节 stocks.yaml 示例(整理进测试夹具)加载成功且各节语义正确
- [ ] 明文 Cookie 样例被拒;非法 cron 被拒;未知字段被拒
- [ ] 每节缺省值有单测覆盖
- [ ] 加载错误信息结构化(字段路径+原因),供 `myia doctor`(v0.2)与 agent 消费

## Notes

- 新建 `src/myia/schema.py`(或 `config.py`,design.md 定);pydantic 可用(校验是刚需,不算重依赖)
- 现有 `pipeline.py` 里对 YAML 的临时读取方式本任务后废弃,统一走 schema 层

## 验收记录(2026-10-03,受主人委托代验)

verdict: accepted

evidence: uv run --no-sync python -m pytest tests/test_schema.py -q → 50 passed;tests/fixtures/stocks.yaml 加载+各节语义断言(test_schema.py:62-129)过,明文 Cookie/非法 cron/未知字段拒载(:208/:277/:311)过,各节缺省 TestDefaults(:138)过,LoadErrorDetail 结构化(path+error_type+中文 message)另经 CLI 实探证实($.schedule/$.sources[0].headers.Cookie);pipeline.py:746 统一走 load_category_file,临时读取已废弃。
