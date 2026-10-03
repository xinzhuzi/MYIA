# 设计:v1.1.1 桌面数据通路(应用数据根统一 + 插件随包 + 首跑种子)

对应 prd.md 修复设计五条;本文将其细化到文件级决策。探查证据见 prd.md 矩阵。

## D1 路径解析收口在 sidecar 层(entry.py),不动 myia.cli 全局默认

CLI 的相对路径默认(`DEFAULT_DB_PATH="myia.db"` / `DEFAULT_PLUGINS_DIR="plugins"`,
cli.py:158-159)是仓库开发契约,大量 CLI 测试依赖。桌面上下文的解析放
`desktop/entry.py` 一处,CLI 零改动零回归。

`entry.py` 新增:

- `myia_home() -> Path`:平台数据根。darwin=`~/Library/Application Support/MYIA`;
  win32=`%APPDATA%\MYIA`(env 缺失回退 `~/AppData/Roaming/MYIA`);linux=`~/.myia`。
- `_serve_context() -> ServeContext`(每请求解析,极廉价;便于测试按 env 注入):
  优先级 **显式 params(既有参数,不变) > `MYIA_HOME` env > bundle 探测 > dev 回退**:
  - `MYIA_HOME` 设定(或 bundle 探测命中,见 D2)→ home 模式:
    `db=<home>/myia.db`、`plugins_dir=<home>/plugins`、
    `install_root=MYIA_PLUGIN_DIR` env(既有约定,不夺权)否则 `<home>/plugins`;
    解析即 `mkdir -p <home>` 与 `<home>/plugins`(全新数据根上 health/doctor
    返回空态 OK 而非 plugins_dir 错误 —— 探查矩阵「无创建逻辑」根因的收口)。
  - 否则 dev 模式:三项默认与今日完全一致(`DEFAULT_DB_PATH` /
    `DEFAULT_PLUGINS_DIR` / `default_install_root()`=「~/.myia/plugins」)。
- 各方法默认值收口( params 缺省时取 context,显式 params 永远赢):
  health/doctor → `plugins_dir`+`db`;plugins.list → `dir`;store.items/run.start → `db`。

## D2 bundle 探测 + 首跑种子(serve 启动时一次)

- **bundle 探测**(仅 `sys.frozen`):exe 位于 .app 内
  (`Path(sys.executable)` 祖先含 `*.app`,darwin)→ home 模式(平台根)。
  作为 Rust 注入丢失时的兜底,与 env 同效。
- **bundle plugins 目录定位** `_bundle_plugins_dir()`:候选
  `<exe>/../Resources/plugins`(macOS .app)、`<exe>/plugins`(win/linux Tauri
  resources 相对结构),取第一个存在且含 `*.yaml` 的;dev/找不到 → None。
- **种子** `serve()` 启动时:home 模式 && `<home>/plugins` 无任何 `*.yaml/*.yml`
  && 标志文件 `<home>/.seeded` 不存在 → 从 bundle 目录拷全部 `*.yaml/*.yml`
  → 写 `.seeded`(内容 ISO 时间)。用户后续删光插件不复种(标志在即不种)。
  拷贝非原子可接受:中途失败最坏半份副本且无标志,下次启动整体重拷覆盖。

## D3 空态语义(health first_run)

- home 模式下 health 后处理:`first_run = <home>/plugins 内零 yaml`(真·首跑,
  种子失败/被删光)。`healthy` 维持 CLI 口径(空目录本就不报错;probe 报错
  根因是目录不存在,统一根后消失)。dev 模式 `first_run=false`。
- plugins.list(市场面)首跑后合法为空,见 D7;不再把它当报障信号。

## D4 Rust 注入 MYIA_HOME(main.rs,一处)

setup 内:`app.path().home_dir()` 按平台拼 D1 同名路径,`create_dir_all` 后
`.env("MYIA_HOME", path)` 挂到 sidecar spawn。与 D2 兜底双保险(优先级里
env 在前)。sidecar run 子进程经 `os.environ.copy()` 自动继承(db 已显式传)。

## D5 打包资源(tauri.conf.json)

`bundle.resources` 增加官方四件套映射 `"../plugins/<id>.yaml": "plugins/<id>.yaml"`
(ai-news/wool/stocks/gpu-prices);保留既有 fixture。不随包:`credentials.yaml`、
`monitor.yaml`(依赖市场插件/凭据,首跑种子会造成 doctor 大量 error 级
findings)与 `community/`(子模块,当前不存在)。

## D6 UI 空态引导 + run 对齐

- `types.ts`:`HealthResult` 增 `first_run?: boolean`。
- feed 屏空态分叉:无条目时——`first_run`(无插件)给「完成初始化/查看源管理」
  引导;有插件无条目给 CTA **「运行第一个插件」**:`health()` 取第一个
  `loaded` 插件的 `file`(修路径后为绝对路径)→ `runStart({yaml})` →
  `completed` 后刷新;`run_busy` 期间按钮置忙。其余屏空态沿用 EmptyState
  组件只调文案(dashboard 品类卡/近期 run、logs 无日志、sources 无源)。
- run.start 参数形态结论:yaml = health 报告的品类 YAML 绝对路径原样回传,
  与 sources.write 的 `file` 同口径(prd.md 待复核项就此闭环)。

## D7 PRD 验收口径修正(记录理由,不静默改)

prd.md「首跑后 plugins.list ≥4 官方插件」按字面不可达成也不应达成:官方四件套
是**品类 YAML**(v1.1 source-managed pivot 后的插件形态,平铺文件,经
health/doctor 可见),而 plugins.list 是**市场面**(InstalledPluginStore 扫
子目录+manifest),市场包 myia-osint 等是子模块、v1.1.1 不随包,且 UI 无任何
pluginsList 调用方。验收修正为:**health.plugins ≥4 且 plugins.list 无错误返回**
(市场面空是合法态)。~/.myia 旧市场安装不迁移(桌面从未提供市场 UI 入口,
无既有用户数据可丢;install_root 随 MYIA_HOME 走属新语义)。

## D8 测试

- pytest(tests/test_desktop_sidecar_protocol.py 增补;既有全量不回归):
  上下文优先级(env>dev;params 显式赢)、bundle 探测(monkeypatch
  sys.frozen/sys.executable)、种子(临时 MYIA_HOME+假 bundle 目录;幂等
  =二跑零新拷;`.seeded` 抑制)、health first_run 真假两态、dev 模式行为
  不变(相对默认仍是 CLI 常量)。
- vitest(ui-src):feed 空态 CTA(mock api:无插件/有插件/run_busy 三态)。
- **真实安装冒烟**(防「mock 层可用」复发):重打 sidecar+tauri build →
  重装 /Applications → cwd=`/` 直接跑包内 serve 二进制重演 prd.md 全方法
  矩阵全 OK;`open` 起 App 验窗口存活,osascript+screencapture 留档尽力。

## 兼容与回滚

- dev(仓库内)行为逐字节不变,由测试锁定;桌面旧用户无数据迁移面(见 D7)。
- 单 commit 修复,回滚=revert;首跑只新增 `<home>` 目录,不触碰仓库与 ~/.myia。
