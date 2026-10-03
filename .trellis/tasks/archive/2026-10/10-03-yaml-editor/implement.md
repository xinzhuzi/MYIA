# 执行计划:桌面端 YAML 配置编辑器

前置:grill Round 1 十项决议已全批(见 prd.md 决议记录);**start 闸门 = release 波(v1.1.1 tag)收尾信号**(决议 1,不插队)。`task.py start` 后才动代码。步骤 = 提交粒度(每步一 commit,回滚 = revert 单步)。

**并行会话协调**:开工时 desktop/ui-src 可能残留先行任务的未合并改动(如 sidebar-brand-trim 系)——开工前 rebase main,侧栏/路由冲突以先合并方为准、后到方适配。

## 步骤

### 1. 协议只读两方法:yaml.list / yaml.read
- entry.py:`_fence_yaml_path` + `_m_yaml_list` + `_m_yaml_read`,注册 `_HANDLERS`
- pytest:正例(list 全量入列/read 原文含注释)+ 围栏四违例 + file_too_large + invalid_encoding
- 验证:`uv run --no-sync python -m pytest tests/test_desktop_sidecar_protocol.py -q`

### 2. 协议干跑:yaml.validate
- entry.py:`_validate_yaml_content` 共享 helper + `_m_yaml_validate`(findings 带 level: error/warning,永不抛校验错);**keychain: 引用对照 secret_list → `secret_unknown` warning**(决议 8;env 不对照)
- pytest:语法错/未知字段/明文凭据/重复键 findings(error 级);keychain 未录入出 warning 且 valid=true;干跑后文件 mtime 不变
- 验证:同上

### 3. 协议写回与生命周期:yaml.save / yaml.template / yaml.delete
- schema.py 微重构:`_ID_RE` 提为公开 `CATEGORY_ID_RE`(一处定义;sidecar/前端规则同源)
- entry.py:`_m_yaml_save`(同门校验→跨文件 id 查重→.bak→原子写;mtime 乐观锁;**null mtime+不存在 = 新建**;warnings 透传)、`_m_yaml_template`(`_CATEGORY_TEMPLATE` 常量)、`_m_yaml_delete`(.bak→删主文件→连带 `.disabled.json`)
- **启停止血**(决议 2):`_m_sources_write` 在 safe_dump 覆盖前 `shutil.copy2` 存 `.bak`(3 行;根治在 10-03-yaml-toggle-comments,勿越界)
- pytest:成功往返(doctor 识别新 schedule)、校验失败零写入且 .bak 不动、mtime_conflict、停到 0 源被拒;新建往返 + file_not_found 分叉 + duplicate_category_id + stem 正则违例;delete 往返(含连带暂存清理);模板必过 load_category;**启停止血**(toggle 后 .bak = 带注释原文)
- 验证:同上 + `uv run --no-sync python -m pytest -q`(全量基线)

### 4. npm 依赖:CodeMirror
- `npm i @uiw/react-codemirror @codemirror/lang-yaml`;即刻 `npm run build` 验 React 19 peer
- 不容 → 退裸 `@codemirror/state/view/language` 自封装(design §6),步骤 5 的 editor-pane 换实现,契约不变
- 验证:`npm run build && npm run test`

### 5. UI:api.ts + 屏骨架 + 文件列表 + 读取
- screens/yaml-editor/{api.ts, yaml-editor-screen.tsx, file-list.tsx, editor-pane.tsx};App.tsx 路由 + 侧栏项
- vitest:list 渲染/坏文件徽标/选中读原文(mock api)
- 验证:`npm run test && npm run build`

### 6. UI:校验面板 + 保存流 + 新建流 + 保存闭环 + dirty 守卫
- findings-panel.tsx(error/warning 分级);「校验」干跑、「保存」流(成功清 dirty / 失败结构化错误);**「新建」流**(stem 预检→模板→草稿态→save(null mtime)→转正常态);**保存闭环**(决议 7:成功后自动 doctor 复核展示 +「跑一次」按钮复用 run.start,dirty 禁用);**完整路径展示**(决议 10);切文件与离开守卫
- vitest:dirty 拦截、保存失败错误态、保存成功态(+自动复核调用)、新建 stem 预检与草稿标记、跑一次 dirty 禁用
- 验证:同上

### 7. 源管理联动 + 删除动作
- sources-table 行动作「编辑」→ Link `/yaml-editor?file=...`;编辑屏 useSearchParams 预选;文件项「删除」(confirm→yaml.delete→刷新)
- vitest:链接渲染 + 预选 + 删除 confirm 流
- 验证:`npm run test`

### 8. 全量回归 + 工程卫生收尾
- `uv run --no-sync python -m pytest -q` + `npm run test` + `npm run build`(**当期基线**,勿死守历史数字)
- 手工冒烟(design §7):dev 模式真文件往返、注释逐字节、doctor 复核、新建后源管理即时可见
- **协议 spec 落档**(Phase 3.3):新建 `.trellis/spec/desktop/sidecar-protocol.md`——sidecar 方法注册表(届时 17 方法)+ 错误码表 +「单一事实源 = entry.py `_HANDLERS`」指针。动机:C7 式注释腐化已复发过一次,17 方法再不落档就是下一轮普查的 C 组素材
- **C7 注释顺手修**:client.ts:108「与 _HANDLERS 一一对应」失实注释更正(封装面 vs 协议面分开表述)
- 更新 prd.md 验收勾选;`domain/yaml-schema.md` 若加「桌面编辑链路」一节在此步

## 回滚点

每步独立 commit;revert 即回。步骤 3 落盘行为仅限 plugins 目录内 `.yaml/.bak`(围栏内),无仓库外副作用。

## 风险与守门

- React 19 peer(步骤 4 第一时间验,有裸 CM6 退路)
- `.disabled.json` 与编辑正交,唯一例外 = yaml.delete 的连带清理(删除品类后暂存无主,留着是脏文件)
- 文件名 stem 正则必须 import schema 公开的 `CATEGORY_ID_RE`,前端预检与后端围栏同源,防两处正则漂移
- Windows 路径分隔符:围栏判定用 `Path.resolve()` + `commonpath`,不做字符串前缀比对
