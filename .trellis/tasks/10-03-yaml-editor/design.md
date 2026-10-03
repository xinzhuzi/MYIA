# 技术设计:桌面端 YAML 配置编辑器

对应 prd.md;分层边界 = sidecar 协议(Python)→ 屏私有 api.ts → 屏组件。零新 Python 依赖;npm 新增 CodeMirror 系(见 §6)。

## 1. 协议契约(钉死;Python/TS 两侧同源,契约注释两侧互指)

### yaml.list

```
req  {}                                    # 目录由 _serve_context 定,不开放任意目录参数
res  { "plugins_dir": str,
       "files": [ { "file": str(绝对), "name": str(文件名),
                    "parse_ok": bool,                 # yaml 语法 + load_category 同门试载
                    "category_id": str|null, "category_name": str|null,
                    "sources": int|null,              # 启用源数(parse_ok 时)
                    "error": {path, code, message}|null } ] }
err  source_dir_unreadable                  # plugins 目录不可读
```

- 目录扫描 `*.yaml`/`*.yml`,排序按文件名;`.disabled.json`/`.bak`/`.seeded` 不入列
- **坏文件也入列**(parse_ok=false + error)——这是编辑器的核心用例之一:修好 health 加载不了的文件

### yaml.read

```
req  { "file": str }
res  { "file": str, "content": str, "size": int, "mtime": float }
err  path_outside_root | file_not_found | invalid_encoding | file_too_large
```

- 原文字节直读:`read_text(encoding="utf-8", newline="")`(失败 → invalid_encoding 结构化);**不经任何 yaml dump 往返**。`newline=""` 是硬要求:Python 默认 universal newlines 会把 CRLF 读成 LF,写回即变字节——「逐字节保真」承诺(AC2)靠它成立
- 上限 1 MiB(file_too_large)——品类 YAML 百行量级,防误开无关大文件

### yaml.validate

```
req  { "content": str, "file": str|null }   # 干跑:file 仅作错误上下文,可不带
res  { "valid": bool,                        # = 无 error 级 finding(warning 不翻假)
       "findings": [ { "path": str("$" 起), "code": str, "message": str,
                       "level": "error" | "warning" } ],
       "category": { "id": str, "name": str, "sources": int } | null }
# 无协议层 err(参数类型非法除外)——校验结果本身就是正常应答
```

- 语法错 → finding `{path:"$", code:"yaml_parse_error", level:"error"}`;schema 错 → `load_category` 的 LoadError 明细透传(path/error_type/message,与 sources.write 的 data.errors 同构),level=error
- **`keychain:` 引用对照 `secret.list`**(决议 8):收集内容中凭据引用(头部 `keychain:` 前缀值),对照已录入名单;未录入 → `{code:"secret_unknown", level:"warning", message 含凭据名与录入命令}`。**warning 不拦保存**——先写 YAML 后 `myia secret set` 补凭据是合法流。`env:` 不对照(名单运行时才知,必误报)
- 实现:`_validate_yaml_content(content, ctx) -> list[finding]` 共享 helper(validate 与 save 同一道逻辑,防两门漂移);`read_yaml_document` 思路复用(_UniqueKeyLoader 重复键检测),再 `load_category`;**不落盘**

### yaml.template

```
req  {}                                    # 零参数
res  { "content": str }                    # 最小合法品类模板(id/name 占位,UI 回填)
# 无业务 err
```

- 模板在 Python 侧维护(schema 的家乡),pytest 锁 `load_category(template)` 必过——schema 演进时模板不会静默腐烂
- 最小模板(实读 schema.py:1144-1156/572-592 定稿):`id/name/schedule + sources[{name, url}]`,engine 缺省 auto、extract 缺省 L3 兜底、push 缺省空表;文件头注释指向 stocks.yaml(权威示例)与字段语义

### yaml.save

```
req  { "file": str, "content": str, "expected_mtime": float|null }
res  { "file": str, "written": true, "created": bool,
       "backed_up": str|null(.bak 路径), "mtime": float,
       "warnings": [finding] }              # error 级零个才会走到这;warning 原样带回(UI 展示)
err  path_outside_root | not_yaml_suffix | invalid_file_stem
   | file_too_large                        # content > 1 MiB(与 read 对称——只堵读不堵写等于没堵)
   | file_not_found                        # file 不存在且 expected_mtime 非 null(不是新建意图却找不到)
   | mtime_conflict                        # 乐观锁:读后文件被外部(CLI/别的窗口)改过
   | duplicate_category_id                 # 跨文件品类 id 撞车(data: {"conflicts": [<file>]})——自建地雷在此收口
   | category_invalid (data.errors=[...])  # 同门校验 error 级,零写入(secret_unknown 这类 warning 不在此列)
   | source_write_failed                   # 备份/落盘 IO 失败
```

- 流程钉序:**围栏+stem 正则 → `_validate_yaml_content`(error 级零容忍零写入;warning 收集透传)→ 跨文件 id 查重 → 旧文件拷 `.bak`(新建无此步)→ `_atomic_write_text`**
- **新建语义**:file 不存在 + `expected_mtime=null` = 创建;file 不存在 + 非 null mtime = `file_not_found`(把「想改却不存在」与「想建」分开,防路径手误建出影子文件)。新建 stem 必须过品类 id 同款正则(见围栏),防 `My Category.yaml` 这类脏名进 plugins 目录
- `expected_mtime` 乐观锁:yaml.read 带回 mtime,save 时对照;不符即 mtime_conflict(UI 提示重读,不覆盖外部改动)
- **跨文件 id 查重**:load_category 通过后取 `doc.id`,扫目录其余 parse_ok 文件的 id;撞车 → `duplicate_category_id`(现状 loader 对重复 id 零守卫,两个 `id: stocks` 会静默混品类——写盘门是唯一能拦的地方)
- 与 `sources.write` 互不调用、互不替代:启停走名单搬运(结构语义),编辑走原文(文本语义);`.disabled.json` 暂存归启停管,编辑器不碰。**唯一交叉 = 决议 2 止血**:sources.write 在 dump 覆盖前先 `shutil.copy2` 存 `.bak`(3 行;根治文本手术在 `10-03-yaml-toggle-comments`)

### yaml.delete

```
req  { "file": str }
res  { "file": str, "deleted": true, "backed_up": str(.bak 路径) }
err  path_outside_root | not_yaml_suffix | file_not_found | source_write_failed
```

- 流程钉序:**围栏 → 拷 `.bak` → 删主文件 → 连带删 `<yaml>.disabled.json`**(有则删;先备份后删,中途崩溃最坏 = 文件还在,无损方向)
- 全删光 = 合法空态:`.seeded` 标志在即**不复种**(entry.py:231-235 语义),feed 空态「运行第一个插件」CTA 引导用「新建」重建——恰好闭环,不需特判

### 路径围栏(全方法共用的 `_fence_yaml_path(file) -> Path`)

1. `Path(file)` 后缀 ∈ {`.yaml`, `.yml`}
2. `resolve()` 后必须位于 `Path(ctx.plugins_dir).resolve()` 之下(`os.path.commonpath` 判定;resolve 已消解符号链接与 `..`)
3. 新建场景加一条:stem(去后缀文件名)必须过品类 id 同正则 `^[a-z0-9][a-z0-9_-]{0,63}$`(schema.py:208 `_ID_RE`;微重构:在 schema.py 把 `_ID_RE` 提为公开 `CATEGORY_ID_RE` 一处定义,sidecar import——不复制正则)
4. 违例 → `path_outside_root`(message 带两个路径,如实展示)
- 动机:sidecar 是 UI 直连的读写通道,不设围栏 = 桌面端任意文件读写原语(违反 `.trellis/spec/domain/security-baseline.md` 精神)

## 2. Python 侧结构(entry.py,全在既有文件内)

- `_fence_yaml_path(params) -> tuple[Path, ServeContext]`:围栏 + ctx
- `_m_yaml_list / _m_yaml_read / _m_yaml_validate / _m_yaml_template / _m_yaml_save / _m_yaml_delete`:六个 `_m_*` 处理器,注册进 `_HANDLERS`
- `_collect_category_ids(exclude: Path) -> dict[str, Path]`:目录扫描取 parse_ok 文件们的 id(save 查重用)
- 模板:`_CATEGORY_TEMPLATE: str` 模块常量(pytest 锁必过 load_category)
- 复用:`_serve_context`、`_atomic_write_text`、`_stash_path`、`ProtocolError`、`load_category`、`read_yaml_document`(schema.py 已导出)
- `.bak` 写法:save/delete 成功路径上 `shutil.copy2(yaml, yaml.with_suffix(yaml.suffix + ".bak"))`;bak 不设上限(单份滚动,品类 YAML KB 级)
- **写侧字节保真**:`_atomic_write_text` 写用户原文时必须 `write_text(..., newline="")`(禁 os.linesep 翻译,Windows 上尤其);该函数与 sources.write 共用,其内容是生成的纯 `\n` 文本,加 `newline=""` 无副作用——统一改,不留两条写入路径

## 3. UI 侧结构(desktop/ui-src/src/)

```
screens/yaml-editor/
  api.ts                  # invoke("sidecar_request") 封装 + 契约类型 + asSidecarError(照抄 sources/api.ts 惯例)
  yaml-editor-screen.tsx  # 双栏布局 + 状态机 + dirty 守卫
  file-list.tsx           # 左栏:文件项(品类名/坏文件徽标/选中态)
  editor-pane.tsx         # CodeMirror 封装(受控 value + onChange)
  findings-panel.tsx      # 校验/保存错误的结构化清单(path+code+message)
```

- 路由:App.tsx 加 `<Route path="yaml-editor" ...>`;侧栏 NAV_ITEMS 加 `{ to: "/yaml-editor", label: "配置编辑", icon: FileCode2 }`
- **新建流**:列表头「新建」→ 输入 stem(前端先过正则预检,失败即拦)→ `yaml.template` 取模板 → UI 回填 `id: <stem>` / `name: 我的品类` → 编辑器进入「未保存草稿」态(dirty;文件名显示 `<stem>.yaml *`)→ 保存走 `yaml.save {file, content, expected_mtime: null}` → 落盘后转正常编辑态并刷新列表
- **保存闭环**(决议 7):保存成功 → 自动 `doctor({yamls:[file]})` 复核,结果一行展示(如「doctor 识别 4 源」/复核失败如实报);**「跑一次」**按钮(头部 actions 区)复用 feed 空态同款 `run.start` 封装,以当前品类 id 发起——**dirty 时禁用**(title 提示「先保存」),发起后引导去日志屏看流。**Cmd+S = 保存**(编辑器无快捷键等于没腿;preventDefault 防 webview 默认行为);**采集运行中提示**:发起保存/跑一次前查 `run.status`,该品类有 in-flight run → 一行提示「采集进行中,改动下一次运行生效」(**只提示不拦**——run 在启动时已读完 YAML,中途改文件无害)
- **stem 输入体验**:新建输入框 placeholder 写明规则(小写字母/数字/`-`/`_`,≤64 字符——中文放 `name:` 字段);预检失败内联报因,不发请求
- **完整路径可见**(决议 10):PageHeader 或编辑器标题区显示当前文件绝对路径(dev 模式即仓库 `plugins/…`,透明自担)
- **删除动作**:文件项 hover「删除」→ confirm(列明品类名、「.bak 留底」、「官方件删除后需重装或从模板重建」——决议 9 文案)→ `yaml.delete` → 刷新列表;选中项被删则编辑器回 idle
- 源管理联动:sources-table 行动作「编辑」→ `<Link to={`/yaml-editor?file=${encodeURIComponent(row.pluginFile)}`} />`;编辑屏挂载时读 `useSearchParams` 预选
- 状态机:`idle(未选文件) → loading → ready/error`;dirty = content !== 已读原文;切文件/路由离开时 dirty 守卫(v1 `window.confirm`,够用不花哨);**空目录态**(用户删光全部品类):文件列表复用 `EmptyState` + 「新建第一个品类」CTA——与 feed 空态引导同款闭环,不是死胡同
- 保存成功后:更新本地 mtime 基线,清 dirty;可选顺手 `doctor({yamls:[file]})` 复核(与源管理屏往返复核同款,标记「myia run 可识别」)

## 4. 注释保真与相邻缺陷的隔离

- 编辑链路(read→save)原文往返,注释/顺序/引号风格逐字节保留;**既有动注释的路径是 `sources.write` 的 `safe_dump`**(prd 相邻地雷)
- 决议 2 定处置:本任务加**止血**(sources.write 在 dump 覆盖前 `shutil.copy2` 存 `.bak`,原文可找回,3 行);**根治(文本手术)已独立立项 `10-03-yaml-toggle-comments`**,本任务不动 sources.write 主逻辑

## 5. 兼容与回滚

- 协议只增不改:`_HANDLERS` 加 6 键,旧 UI/旧测试零感知;新 UI 打旧 sidecar → 结构化 `method_not_found`(与 sources.write 收编前同款降级,UI 按错误态如实展示)
- 回滚:UI 屏/路由/侧栏自成一块,revert 即回;协议新方法无持久副作用(除用户主动新建/保存/删除的内容与 `.bak`)

## 6. 依赖决策

- npm:`@uiw/react-codemirror@4.25.12` + `@codemirror/lang-yaml`(2026-10-03 npm 实测 peer `react>=17.0.0`,React 19 兼容——决议 5 已拍)。裸 CM6(`@codemirror/state/view/language` 自封装)仅作 build 翻车退路,不预先付成本
- Python:零新依赖(PyYAML/pydantic 已有;核心 6 依赖封顶红线不破)
- 主题:CM6 用 oneDark 或按现有 Tailwind 暗色 token 轻定制;以「与五屏现有观感协调」为验收眼,不引重主题包

## 7. 测试策略

- **协议(pytest,tests/test_desktop_sidecar_protocol.py 沿用既有夹具风格)**:每方法正例;围栏四违例(穿越/绝对路径/后缀/符号链接)+ 新建 stem 违例(大写/空格/超长);坏 YAML list 入列 + read 可开 + save 拒写;validate 干跑后文件 mtime/内容不变;save 的 `.bak` 内容 = 旧原文;mtime_conflict;last-source 守卫经 load_category 生效(停到 0 源的编辑保存被拒);**新建往返**(null mtime → created=true → list/doctor 识别)、**file_not_found 分叉**(不存在 + 非 null mtime)、**duplicate_category_id**(两文件同 id 拒写)、**delete 往返**(主文件 + `.disabled.json` 连带删、`.bak` 留底)、**模板必过 load_category**(schema 演进防腐锁);**findings 分级**(error 拦保存;`secret_unknown` warning 放行且 save 应答带回——keychain 对照用既有 secret 测试夹具);**启停止血**(sources.write 覆盖前 `.bak` = 带注释原文);**并发互斥**(先 sources.write 再编辑器 save → mtime_conflict,UI 引导重读——两条写路径靠乐观锁互斥,不靠运气)
- 手工冒烟补:新建品类保存后,源管理/仪表屏**即时**可见新品类(health 逐请求目录扫描,零重启零刷新等待)
- **UI(vitest,jsdom)**:list 渲染 + 坏文件徽标;选中读文件渲染原文(mock api);dirty 守卫拦截切换;保存失败结构化错误态;源管理「编辑」链接带 file 参数;**新建流**(stem 预检/模板渲染/草稿态标记)、**删除 confirm 与列表刷新**(含官方件提示文案)、**保存后自动 doctor 复核调用与展示**、**「跑一次」dirty 禁用与发起**、**完整路径展示**
- **回归**:`uv run --no-sync python -m pytest -q` + `desktop/ui-src` `npm run test` + `npm run build`(tsc -b)——基线数字以开工时实测为准(prd AC9)
- 手工冒烟(dev 模式):改 schedule → doctor 识别;注释逐字节保留(diff);跑一次 → 日志屏有流
