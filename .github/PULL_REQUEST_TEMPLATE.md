<!-- 标题用英文祈使句,例如 "feat: add per-source proxy pool hint" / "fix: …"。 -->

## What / 做了什么

<!-- 一两句话说明改动;一个 PR 一个子任务粒度。 -->

## Why / 为什么

<!-- 关联 issue:#123;若无 issue,说明动机。 -->

## How verified / 怎么验证的

<!-- 贴出实际运行过的命令与结果,例如:
uv run --no-sync python -m pytest tests/<module>/test_xxx.py -q   → N passed
uv run --no-sync python -m pytest -q                     → 全量回归结果
-->

## Checklist / 自检清单

- [ ] 测试全绿:`uv run --no-sync python -m pytest -q`(全量,不只新增文件)
- [ ] 新增/修改行为有对应测试;引擎测试零真实网络(录制回放/注入假模块)
- [ ] **凭据零明文**:代码、YAML、测试、文档中凭据位只写 `env:VAR` / `keychain:myia/<scope>/<name>` 引用
- [ ] 错误结构化(字段路径+错误类+原因),退出码符合契约(0 成功 / 1 配置错误 / 2 全部失败 / 3 部分失败)
- [ ] 可选依赖(crawl4ai/scrapling/firecrawl/skyvern/openai)只做惰性 import,未新增任何核心依赖(新增依赖需 PRD 论证记录)
- [ ] 若改了 schema:四处同步无漂移——`src/myssia/schema.py`、`skill/SKILL.md`、`docs/`、`plugins/` 示例(`tests/test_docs.py` / `tests/test_skill_doc.py` 会锁)
- [ ] 文件/标识符英文;错误与日志文案中文;日志不含凭据值
- [ ] 本 PR 全文(含 diff)无凭据、内网地址、生产语料、私有系统痕迹
