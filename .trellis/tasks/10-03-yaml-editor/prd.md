# PRD:桌面端 YAML 配置编辑器(品类 YAML 可视化查看与编辑)

## 背景

主人 2026-10-03 指示(原文):「这个项目中,必须要有yaml编辑器啊,可以让人看出来yaml里面写的什么也可以让人去修改,必须加入相关的插件,做一个yaml的编辑模块!做个trellis任务文档,探索现在的项目,看看做到哪里合适!」

品类 YAML(`plugins/*.yaml`)是 MYIA 的核心用户配置面:12 节 schema、重中文注释、AI/人双读者(见 `.trellis/spec/domain/yaml-schema.md`)。当前桌面端五屏只有源健康度 + 启停开关——**看不到 YAML 内容、改不了**;CLI 用户可手编,桌面用户无入口。本任务补上这块。

## 探查结论(2026-10-03 本会话实读,非转述)

### YAML 文件全景(谁是编辑对象)

| 文件 | 角色 | 本任务 v1 |
|---|---|---|
| `plugins/*.yaml`(仓库 6 份:ai-news/stocks/wool/monitor/credentials/gpu-prices) | 品类配置,用户可改 | ✅ 编辑对象。dev 模式即 repo/plugins;打包模式为首跑种子副本 `MYIA_HOME/plugins`(entry.py:223-249,种子幂等由 `.seeded` 保证) |
| `plugins/myia-*/plugin.yaml` | 插件清单(市场/安装机制管理,manifest.py/installed.py) | ❌ 不编辑 |
| 全局配置 YAML(pools 代理声明,CLI `--config` 可选路径) | 全局配置 | ⏸ 桌面端尚无既定落点,建议 v1 不含(待拍板 3) |

### 现有通路与扩展点(全部实读核实)

- **sidecar 协议**:`desktop/entry.py` `_HANDLERS`(entry.py:910-922)11 方法,JSON-line stdio,结构化错误(ProtocolError → `{code, message, path, data}`)。新增方法扩展点干净——`sources.write` 即先例(UI 曾以「协议扩展提案」先行,后端收编,见 sources/api.ts 模块头)
- **已有写回仅启停**:`sources.write`(entry.py:585-708)按名单在 `sources:` 节与 `<yaml>.disabled.json` 暂存间搬运源节点,不触其余内容
- **校验同门**:`load_category`(schema.py:1312,与 `myia run` 同一道门;错误带字段路径;`_UniqueKeyLoader` 重复键检测 schema.py:1488);`doctor({yamls:[file]})` 可复核(UI 已有封装)
- **原子写先例**:`_atomic_write_text`(entry.py:578,同目录 tmp + `os.replace`)
- **UI**:五屏路由(App.tsx),`screens/<name>/` 自带 api.ts + vitest;源管理表格行已带品类文件路径(`pluginFile`)——天然的编辑跳转入口
- **编辑器库:零**。package.json 无任何编辑器依赖
- **数据装配惯例**:屏私有 api.ts 走 `invoke("sidecar_request")` + `asSidecarError` 归一化(sources/api.ts:122-160 可照抄)
- **自建现状 = 桌面零入口**:创建新品类只能 CLI 用户手拷 YAML 进数据根(极客路径);`add-source` CLI 是 v0.2 stub(普查 F 组);插件市场是分发面不是创作面。跨文件**重复品类 id 无任何守卫**(cli.py/manifest.py/installed.py grep 零命中)——手拷官方件改源忘改 id 会静默混品类
- **最小合法品类**(schema.py:1144-1156 实读):必填仅 `id/name/schedule + sources`(源必填仅 `name/url`,engine 缺省 auto、extract 缺省走 L3 兜底、push 缺省空表)——新建模板可以很小;品类 id 正则 `^[a-z0-9][a-z0-9_-]{0,63}$`(schema.py:208)可直接复用为文件名 stem 规则

### 相邻地雷(顺带登记,非本任务必改)

- **启停抹注释**:`sources.write` 写回用 `yaml.safe_dump`(entry.py:687)——用户点一次启停开关,该品类 YAML 的**全部注释被抹掉**(官方 YAML 恰恰大半是注释:monitor.yaml 63 行约 2/3 是注释)。编辑器走原文读写天然保注释,但开关路径的此缺陷独立存在 → **待拍板 1**:并入本任务修,还是另立小任务
- 缺口普查 **C11**(settings 三表单不可写回,「无 yaml.write 泛化方法」)在 `yaml.save` 落地后有了共用通路;settings 是否接线不属本任务
- 缺口普查 **C13**(源管理无增删改)由本任务全链路收口:增=新建、删=删除、改=原文编辑;「试抓此源」仍归 C13 另议

### 「插件」一词的解读(重要,请主人过目)

MYIA 的插件系统(`myia-*`)是**数据源场景插件**(plugin.yaml 声明 modes/engines,市场安装),**没有 UI 插件机制**——YAML 编辑器无法、也不应以 myia 插件形态存在。本任务把「必须加入相关的插件」落在**编辑器组件库 CodeMirror 6**(`@codemirror/lang-yaml` 语法高亮;React 封装 `@uiw/react-codemirror`;体积 ~150KB 级,远轻于 Monaco ~2MB 级)。若主人本意是「UI 可插拔机制」,那是 v1.2+ 架构议题,请另行示意,本档不覆盖。

## 方案取舍(做到哪里合适)

| 形态 | 优 | 劣 | 结论 |
|---|---|---|---|
| **独立第六屏**(左文件列表 + 右编辑器) | 范围清晰;全文件编辑的自然形态;坏 YAML 也能打开修复 | 侧栏 +1 项 | ✅ 推荐,路由 `/yaml-editor`,导航名**「配置编辑」**(直白命名) |
| 源管理屏内嵌抽屉 | 就近上下文 | 抽屉空间小;源管理职责(健康度/启停)被搅浑 | 只在表格行放「编辑」跳转入口 |
| settings 屏分区 | 与表单互补 | settings 是 env/凭据表单面,放原文编辑器违和 | ❌ |

## Requirements

### v1 内(本任务)

1. **sidecar 协议扩展 6 方法**(与 `sources.write` 同风格):
   - `yaml.list`:plugins 目录下 `*.yaml/*.yml` 清单(**目录扫描**,不取自 health——语法坏的文件也要可见可修);每项带解析状态与品类名
   - `yaml.read {file}`:原文返回(UTF-8 文本,**不经 dump,注释/顺序逐字节原样**)
   - `yaml.validate {content, file?}`:干跑校验(YAML 语法 + `load_category` 同门),返回结构化 findings(**带级别**:`error`=语法/schema 错,`warning`=不拦保存),零写入。**`keychain:` 引用对照 `secret.list`**:未录入 → `secret_unknown` warning(决议 8);`env:` 只验格式
   - `yaml.template {}`:最小合法品类模板文本(**协议侧维护**——schema 的家乡在 Python,模板随 schema 同仓演进,pytest 锁「模板必过 load_category」;id/name 由 UI 按用户输入回填)
   - `yaml.save {file, content, expected_mtime}`:同门校验(**error 级零容忍零写入,warning 级放行且应答带回**)→ 备份 `<yaml>.bak` → `_atomic_write_text` 原子落盘。**file 不存在 + expected_mtime=null = 新建**(围栏/后缀/文件名正则照走;存在则 mtime 乐观锁);写盘前跨文件查**重复品类 id**(静默混品类地雷在此收口)
   - `yaml.delete {file}`:围栏 → 备份 `.bak` → 删文件 + 连带删 `.disabled.json`(自建生命周期收尾)
   - **路径围栏**(安全底线:UI 不得成为任意文件读写通道):`file` 经 resolve 后必须位于 plugins 目录内、后缀 `.yaml/.yml`,拒绝 `../` 穿越与符号链接逃逸;**新建文件名 stem 与品类 id 同正则** `^[a-z0-9][a-z0-9_-]{0,63}$`
2. **UI 第六屏「配置编辑」**:左文件列表(品类名/坏文件徽标/选中态)+ 右 CodeMirror 编辑器(YAML 高亮、行号);**「新建」入口**(输入名 → 模板 → 编辑器,保存即建);文件项**「删除」动作**(confirm 文案含「.bak 留底」与「官方件删除后需重装或从模板重建」,决议 9);脏状态守卫(切换/离开提示);「校验」按钮(findings 按 error/warning 分级展示);「保存」(失败展示结构化错误,绝不假装成功);**保存成功后自动 doctor 复核 +「跑一次」按钮**(复用 `run.start`,dirty 时禁用;决议 7);**Cmd+S 保存**;采集运行中提示「改动下一次运行生效」(只提示不拦);标题区**显示完整文件路径**(dev 模式即仓库路径,透明;决议 10)
3. **源管理屏联动**:表格行「编辑」动作 → 跳配置编辑屏并预选该文件
4. **启停止血**(决议 2):`sources.write` 写回前先把原文存 `<yaml>.bak`(根治在 `10-03-yaml-toggle-comments`,本任务只兜底)
5. **测试**:协议契约测试进 `tests/test_desktop_sidecar_protocol.py`;UI vitest 用例;`tsc -b` 零错

### 刻意不做(v1,防蔓延)

- 表单化/schema 向导编辑(原文编辑先行;表单是另一档投入)
- `plugin.yaml`(插件清单)编辑、插件市场 UI(普查 C6 另档)
- 全局 pools config 编辑(待拍板 3)
- 撤销历史、多文件 diff、云端同步
- CM6 行内 lint 标记(findings 只有字段路径无行号;v1 列表面板,行内标记排后续)
- 其余缺口普查项(secret.delete、runs.list 等)各归各档

## Acceptance Criteria

1. dev 模式打开配置编辑屏:能看到品类 YAML 全文,**注释完整**
2. 编辑 `schedule` 保存 → `doctor({yamls:[file]})` 识别新值;原文其余内容(含注释)逐字节不变
3. 保存坏内容(语法错 / 未知字段 / 明文凭据)→ 结构化错误展示,目标文件零变更,`.bak` 不动
4. 路径穿越(`../` 逃逸、绝对路径、非 yaml 后缀)→ 结构化拒绝
5. `yaml.validate` 干跑不落盘(findings 如实返回)
6. **新建往返**:「新建」→ 编辑 → 保存 → `yaml.list`/`doctor` 识别新品类(六份官方件 + 1 不混);`.bak` 不误伤既有文件
7. **重复 id 拒绝**:新建内容 id 与既有品类撞车 → `duplicate_category_id` 结构化错误,零写入
8. **删除往返**:删除自建品类 → list/health 不再列;`.bak` 留底;连带 `.disabled.json` 一并清
9. 基线不回归:**当期** pytest / vitest 基线全绿(新增另计;基线数字以开工时实测为准——并行 release 波可能已移动基线,勿死守 1397/40)
10. 源管理行「编辑」→ 跳转配置编辑屏且预选该品类文件
11. **启停止血**:点一次启停后,`.bak` 保有操作前的带注释原文(主文件仍被 safe_dump 重写——已知缺陷,根治在 `10-03-yaml-toggle-comments`)
12. 保存成功后自动 doctor 复核有结果展示;「跑一次」dirty 时禁用,发起后可在日志屏查看运行
13. `keychain:` 引用未录入 → warning 级 finding 展示且保存不被拦;`env:` 引用不做存在性对照

## 决议记录(grill Round 1,2026-10-03;主人批复「按建议落实到任务文档」)

1. **排期**:文档本轮定稿至「可 start」;开工等 release 波(v1.1.1 tag)收尾信号,不插队(grill R1「发布工程先行」不动摇)
2. **启停抹注释**:本任务只加**止血**(`sources.write` 写回前先存 `.bak`,注释丢了可找回);根治(文本手术)拆独立任务 **`10-03-yaml-toggle-comments`**(已建,P2)
3. **备份策略**:`.bak` 单份滚动。用户数据根不在 git,备份必须存在;时间戳多份的清理负担不背
4. **全局 pools config**:v1 不纳入(上游「桌面端全局配置落点」未定,属 settings/代理池议题,C11 后半)
5. **编辑器依赖**:引入 `@uiw/react-codemirror@4.25.12` + `@codemirror/lang-yaml`(npm 实测 peer `react>=17.0.0`,**React 19 兼容**);裸 CM6 降为 build 翻车时的退路,不预先付成本
6. **新建模板**:单最小模板(注释指向 stocks.yaml 权威示例);多场景模板 backlog
7. **保存闭环**:保存成功后自动 `doctor({yamls:[file]})` 复核(「myia 真认」的证据)+ 屏内**「跑一次」**按钮(复用 `run.start` 现成封装;dirty 时禁用)
8. **凭据校验深度**:`keychain:` 引用对照 `secret.list`,未录入 → **warning 级 finding(不拒写**——先写 YAML 后补凭据是合法流);`env:` 只验格式(运行时才知道名单,对照必误报)
9. **官方件删除**:v1 无恢复按钮;删除 confirm 文案写明「官方件删除后需重装或从模板重建」;恢复按钮 backlog
10. **dev 模式**:允许编辑仓库 `plugins/`(dev 的用户就是开发者);编辑器标题区显示完整路径保透明

### Backlog(本轮显式不做,已记录防重复立项)

- 多场景新建模板(列表页/API/监控三选)
- 「恢复官方件」按钮(bundle 重拷)
- settings 三表单接 `yaml.save` 写回(普查 C11 后半)
- 启停注释保真根治 → 已独立立项 `10-03-yaml-toggle-comments`(非 backlog,见决议 2)
