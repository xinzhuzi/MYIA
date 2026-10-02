# src/ 骨架范围与依赖边界(2026-10-01 主人问询记录)

## 问询

> src/ 这个下面是其他框架的引用吗?还是你要怎么做?

## 事实核查(当日审计结论)

- `src/myia/` 共 30 个 Python 文件、约 650 行,**全部是 MYIA 自有代码骨架**(薄壳:签名/数据类/TODO 占位),无任何外部框架源码、无 git 子模块、无 vendored 代码
- 全部 import 只有:Python 标准库 + httpx + selectolax(pyproject 核心依赖中的两个)
- `pyproject.toml` 依赖分两层:
  - 核心必装(5 个轻量库):httpx / selectolax / PyYAML / APScheduler / pydantic——`pip install myia` 即跑,符合规划"零重依赖"底线
  - 引擎可选(extras):crawl4ai / scrapling / firecrawl-py / skyvern / openai——**只 import 对方的库写适配层,不复制对方源码进仓库**
- 四个引擎壳(crawl4ai.py / scrapling.py / stealth_browser.py / llm_browser.py)当前连第三方库都未 import,纯占位;实装排期:v0.1(engine-l1-l2 / engine-firecrawl)→ v0.2(crawl4ai)→ v0.3(scrapling)→ v0.4(stealth / llm_browser)

## 结论与做法

1. v0.1 开工 = **向现有壳填充自研实现**(fetch_base 限速/指纹、registry 降级、schema 校验等),不是引入框架
2. 外部引擎(crawl4ai 等)到对应版本任务时以**库适配层**接入:`myia[<name>]` 可选安装,未装报结构化错误并提示安装命令;永不把对方源码搬进仓库(许可红线:全 MIT/Apache/BSD 库;体积红线:核心 pip install 即跑)
3. 该边界已固化进 `.trellis/spec/python/index.md`「依赖分层与 src/ 边界」节,对所有实现任务生效
