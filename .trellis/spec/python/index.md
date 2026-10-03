# Python 工程约定

## 技术底座

- 纯 Python + uv;核心**零重依赖**:SQLite 单文件(不用 Redis/PG),重引擎(crawl4ai/scrapling/skyvern)一律 extras 可选依赖(`shishi[crawl4ai]`,发行名 shishi),未装时结构化报错并提示安装命令
- 核心依赖共 **6 个**:httpx、selectolax、PyYAML、APScheduler、pydantic、**jinja2**(推送模板渲染,SandboxedEnvironment 沙箱;2026-10-01 grill Q2 定案);除这 6 个外,新核心依赖进 PRD 论证后才加
- 全异步采集:`httpx.AsyncClient`;调度 APScheduler(进程内,不引编排平台——规划定案)
- Python 3.11+;类型注解全覆盖;pyproject.toml 为唯一配置源

## 依赖分层与 src/ 边界(2026-10-01 主人问询后定案)

- `src/` 下**全部是 MYIA 自有代码,不含任何外部框架的引用或复制源码**;也没有 git 子模块/vendored 代码
- 外部项目只以两种方式接入:
  1. **核心必装轻量依赖**(pyproject `dependencies`,5 个):httpx、selectolax、PyYAML、APScheduler、pydantic——运行时作为库调用
  2. **引擎可选依赖**(pyproject `[project.optional-dependencies]`):crawl4ai / scrapling / firecrawl-py / skyvern / openai / vision(看图:ocrmac + rapidocr-onnxruntime + openai)——只 **import 对方的库写适配层,永不复制对方源码进仓库**(许可与体积红线);按 roadmap 分版本实装,v0.1 只做 L1/L2 + firecrawl
- engines/ 下 crawl4ai.py、scrapling.py、stealth_browser.py、llm_browser.py 当前为占位壳(仅 `__future__` import,未引任何第三方库);实装排期见各 `vXX-engine-*` 任务,提前实装属越界
- 判断一个依赖放核心还是 extras 的标准:核心流水线(L1/L2+分类+推送)能跑 = 核心依赖;只有特定引擎/通道需要 = extras

## 目录结构(src/myia/)

```
cli.py          命令入口(输出对 AI/人类双友好)
schema.py       12 节品类 YAML 模型与校验 + images: sidecar 节(管线图片处理环开关;detail_fetch/detail_max_items(10-03-detail-images)= 无图条目详情页追抓:开时 fetch 尾部按管线顺序追抓、HTML 同域 <img> 写回 metadata.images 进同一识图环,每 run 上限 detail_max_items(1-50,缺省 10)、串行每请求 ≥1s、10s/页超时,失败只写 metadata.detail_status 绝不阻管线,源级 images_detail_max_items 覆写 = 该源独立预算;$.images 错误前缀,不入 12 节契约)(AI 写 YAML 的地基)
pipeline.py     编排:fetch→classify→dedup→analyze→push(自研 ~200 行量级)
engines/        六层引擎:fetch_base(公共底座)+ registry(降级编排,含零结果 L3 首遇探测:auto 链 static_html 真零结果且无 hint 的首遇源一次性降级 crawl4ai 探测(30s 短帽、单 run 预算 3=`FetchContext.l3_probe_budget`),出条落 L3 回写 hint、零条/异常回滚空页语义零误报;指纹 skip 与显式 engine 配置永不探测,存量 hint 源零打扰;10-04-crawl4ai-l3)+ L1-L6 各文件一一对应
classify/       builtin(七大类+双信号,数据与代码分离)/ custom(YAML 规则)
dedup.py store/ SQLite + 去重注册表 + 变更基线;接口可插拔(PG 留位)
enrich/         LLM 精评(批量/缓存/预算护栏)
push/           通道(feishu_card/telegram/webhook/stdout)+ 阈值分级路由 + 消息平台层(directory/targets/delivery:通道目录+对象解析+定向投递)
vision/         看图:双引擎 OCR(ocrmac+rapidocr-onnxruntime)+ OpenAI 兼容 VisionClient + vision.yaml 配置 + collect.py 管线图片处理环(fetch 尾部下载→OCR→可选 VL 描述,品类 images: 节驱动,降级只写 image_status 绝不阻管线)+ models.py 模型仓管(HF mlx-community 直下免 convert:snapshot_download+local_dir 断点续传、HfApi 预检磁盘不足即拒、清单/删除/激活,huggingface-hub 惰性 import 在 extras)+ server.py mlx_vlm.server 代管(status 2s 探 / ensure 自起+健康等待 ≤120s:并发互斥锁、超窗杀孤儿不留、日志 >5MB 轮转;失败结构化上抛绝不阻管线)(extras myia[vision],惰性 import;10-03-vision-v2)
```

- 现有文件多为薄壳:任务是**填充**而非新建;新模块先在对应 PRD 登记
- plugins/*.yaml 是 schema 的端到端测试:发现 schema 缺口先回改 schema,不许插件私加字段

## 消息平台层(2026-10-03 定案,task 10-03-hermes-messaging)

- **蓝本移植,不 vendor 原文**:源自 Hermes(NousResearch/Hermes-Agent,MIT)gateway 的通道目录/对象解析/定向投递逐文件重写为 MYIA 风格,模块 docstring 标注上游文件路径与 MIT 归属,上游对照表登记在各子任务档;不整块拷贝原文、不引 git 子模块。各平台一律 httpx 直连官方 API,不引平台 SDK(核心 6 依赖红线不动);接不上官方 API 的平台进 extras 并结构化报错
- **通道目录**(`push/directory.py`):`ChannelEntry(platform, chat_id, name, type, thread_id, last_seen)`;数据根下 `channel_directory.json`(tmp+rename 原子写)+ `channel_aliases.json` 别名覆盖层(load 与重建双向生效,重建后别名仍在——Hermes 同款回归点);重建为按平台桶整体替换,被动平台(Telegram 无列表 API)靠 `merge_entries` 增量积累;手工直编目录文件不保证保留,别名文件才是持久覆盖层;损坏/不可写退化为内存态,绝不阻塞推送
- **对象解析**(`push/targets.py`):spec 形态 `platform:名称或id`,解析顺序 = 显式 id/@username 直达(平台 `parse_direct_ref` 钩子,不经目录)→ 目录精确 id → 精确名(大小写不敏感)→ 唯一前缀(多义即未命中);别名是目录改名层,不是独立解析层级;未命中抛 `TargetResolveError` 内嵌候选列表(MYIA 增量,上游靠交互式 list);纯字符串/前缀匹配,无任何 eval
- **定向投递 + 死信**(`push/delivery.py`):按解析后对象逐一发送(immediate 单条 / digest 每(通道×对象)一卡),单对象失败不阻断同批;死信为错误分类制(Hermes 原味):`forbidden` 与 chat 级 `not_found` 单次硬失败即标 dead,瞬态错误(超时/网络/限流)不标;dead 期间跳过并记结构化日志(不发告警卡);投递成功一次即自愈;无阈值、无配置口
- **schema 同平台约束**:`push[].targets` / `push[].route[].targets` 元素平台前缀须与条目通道一致(内置字面映射 `feishu_card→feishu`、`telegram→telegram`),不一致在配置加载期即拒;跨平台 = 写多条 push 条目;targets 在场时 legacy `target` 可省;不配 targets = 现行为零迁移(golden 回测逐字节等价)
- **注册与刷新**:平台适配器挂现有 `Channel` 协议(`supports_targeting` / `parse_direct_ref` / `discover_directory`),`PLATFORMS` dict 注册表与 `CHANNELS` 并排(feishu/telegram 已接入);刷新三层 = run 前节流懒刷(>5 分钟且有已注册平台才发现,失败退回旧目录+告警)+ CLI `myia channels refresh/list` + 桌面按钮;无发现 API 的被动平台归 passive 上报(退出码 0),非失败

## 桌面发行数据根(v1.1.1 定案,task 10-03-v111-desktop-paths)

- **CLI 相对路径默认(`myia.db`/`plugins`)是仓库开发契约,永不在 CLI 层改动**;桌面上下文的解析全部收口在 `desktop/entry.py` 的 `_serve_context()`,优先级:显式 params > `MYIA_HOME` env(Tauri 壳 main.rs spawn 注入)> 冻结 .app bundle 探测 > dev 回退 cwd
- 平台数据根 `myia_home()`:macOS `~/Library/Application Support/MYIA` / Windows `%APPDATA%\MYIA` / Linux `~/.myia`;home 模式解析即建 `<home>` 与 `<home>/plugins`(全新数据根上 health/doctor 必须空态 OK,不得 plugins_dir 报错)
- 首跑种子:home 模式 && plugins 空 && 无 `.seeded` 标志 → 拷随包 `Resources/plugins/*.yaml`(官方品类 YAML 四件套);标志在即永不复种(尊重用户删除)。dev 模式零动作
- 市场面(`myia plugin list`,InstalledPluginStore)与品类 YAML 平铺共用 `<home>/plugins` 不冲突(health 扫描非递归);官方插件 = 品类 YAML 形态,经 health/doctor 可见,plugins.list 首跑空是合法态
- 改路径行为必须同步 `tests/test_desktop_sidecar_protocol.py` 的上下文/种子用例;发布前必跑「真实安装冒烟」(cwd=/ 全方法矩阵),mock 层绿不算数(v1.1 教训)
