# Python 工程约定

## 技术底座

- 纯 Python + uv;核心**零重依赖**:SQLite 单文件(不用 Redis/PG),重引擎(crawl4ai/scrapling/skyvern)一律 extras 可选依赖(`myia[crawl4ai]`),未装时结构化报错并提示安装命令
- 核心依赖共 **6 个**:httpx、selectolax、PyYAML、APScheduler、pydantic、**jinja2**(推送模板渲染,SandboxedEnvironment 沙箱;2026-10-01 grill Q2 定案);除这 6 个外,新核心依赖进 PRD 论证后才加
- 全异步采集:`httpx.AsyncClient`;调度 APScheduler(进程内,不引编排平台——规划定案)
- Python 3.11+;类型注解全覆盖;pyproject.toml 为唯一配置源

## 依赖分层与 src/ 边界(2026-10-01 主人问询后定案)

- `src/` 下**全部是 MYIA 自有代码,不含任何外部框架的引用或复制源码**;也没有 git 子模块/vendored 代码
- 外部项目只以两种方式接入:
  1. **核心必装轻量依赖**(pyproject `dependencies`,5 个):httpx、selectolax、PyYAML、APScheduler、pydantic——运行时作为库调用
  2. **引擎可选依赖**(pyproject `[project.optional-dependencies]`):crawl4ai / scrapling / firecrawl-py / skyvern / openai——只 **import 对方的库写适配层,永不复制对方源码进仓库**(许可与体积红线);按 roadmap 分版本实装,v0.1 只做 L1/L2 + firecrawl
- engines/ 下 crawl4ai.py、scrapling.py、stealth_browser.py、llm_browser.py 当前为占位壳(仅 `__future__` import,未引任何第三方库);实装排期见各 `vXX-engine-*` 任务,提前实装属越界
- 判断一个依赖放核心还是 extras 的标准:核心流水线(L1/L2+分类+推送)能跑 = 核心依赖;只有特定引擎/通道需要 = extras

## 目录结构(src/myia/)

```
cli.py          命令入口(输出对 AI/人类双友好)
schema.py       12 节品类 YAML 模型与校验(AI 写 YAML 的地基)
pipeline.py     编排:fetch→classify→dedup→analyze→push(自研 ~200 行量级)
engines/        六层引擎:fetch_base(公共底座)+ registry(降级编排)+ L1-L6 各文件一一对应
classify/       builtin(七大类+双信号,数据与代码分离)/ custom(YAML 规则)
dedup.py store/ SQLite + 去重注册表 + 变更基线;接口可插拔(PG 留位)
enrich/         LLM 精评(批量/缓存/预算护栏)
push/           通道(feishu_card/telegram/webhook/stdout)+ 阈值分级路由
```

- 现有文件多为薄壳:任务是**填充**而非新建;新模块先在对应 PRD 登记
- plugins/*.yaml 是 schema 的端到端测试:发现 schema 缺口先回改 schema,不许插件私加字段

## 桌面发行数据根(v1.1.1 定案,task 10-03-v111-desktop-paths)

- **CLI 相对路径默认(`myia.db`/`plugins`)是仓库开发契约,永不在 CLI 层改动**;桌面上下文的解析全部收口在 `desktop/entry.py` 的 `_serve_context()`,优先级:显式 params > `MYIA_HOME` env(Tauri 壳 main.rs spawn 注入)> 冻结 .app bundle 探测 > dev 回退 cwd
- 平台数据根 `myia_home()`:macOS `~/Library/Application Support/MYIA` / Windows `%APPDATA%\MYIA` / Linux `~/.myia`;home 模式解析即建 `<home>` 与 `<home>/plugins`(全新数据根上 health/doctor 必须空态 OK,不得 plugins_dir 报错)
- 首跑种子:home 模式 && plugins 空 && 无 `.seeded` 标志 → 拷随包 `Resources/plugins/*.yaml`(官方品类 YAML 四件套);标志在即永不复种(尊重用户删除)。dev 模式零动作
- 市场面(`myia plugin list`,InstalledPluginStore)与品类 YAML 平铺共用 `<home>/plugins` 不冲突(health 扫描非递归);官方插件 = 品类 YAML 形态,经 health/doctor 可见,plugins.list 首跑空是合法态
- 改路径行为必须同步 `tests/test_desktop_sidecar_protocol.py` 的上下文/种子用例;发布前必跑「真实安装冒烟」(cwd=/ 全方法矩阵),mock 层绿不算数(v1.1 教训)
