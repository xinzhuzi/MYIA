# 执行计划:v1.1.1 桌面数据通路

按 design.md D1-D8 顺序施工;每步后跑对应验证,最后统一全量 + 冒烟 + 收尾。

## 步骤

1. **entry.py:路径上下文 + 种子 + 空态**
   - [ ] `myia_home()` 平台根(darwin/win/linux,见 D1)
   - [ ] `ServeContext` + `_serve_context()` 四级优先级;home 模式 mkdir
   - [ ] `_bundle_plugins_dir()` + `_seed_first_run()`(serve 启动挂一次)
   - [ ] 五方法默认值收口(health/doctor/plugins.list/store.items/run.start)
   - [ ] health 后处理 `first_run`;模块 docstring 协议注记同步
   - 验证:`uv run pytest tests/test_desktop_sidecar_protocol.py -x -q` 全绿

2. **main.rs:MYIA_HOME 注入**
   - [ ] setup 内按平台拼 `~/Library/Application Support/MYIA` /
         `%APPDATA%\MYIA` / `~/.myia`,`create_dir_all`,spawn 挂 `.env()`
   - 验证:`npm run tauri build` 前 `cargo check`(src-tauri 内)

3. **tauri.conf.json:官方插件随包**
   - [ ] resources 增四件套映射(D5)

4. **ui-src:空态引导 + run CTA + 类型**
   - [ ] `types.ts` HealthResult.first_run
   - [ ] feed 空态分叉 + 「运行第一个插件」CTA(D6);其余屏文案微调
   - 验证:`npm --prefix ui-src run test` / `npm --prefix ui-src run build`

5. **pytest 增补(D8 清单)+ 全量**
   - [ ] 优先级/种子幂等/first_run/dev 不变 各单测
   - 验证:`uv run pytest -q` 全绿

6. **重打包 + 真实安装冒烟**
   - [ ] `npm run sidecar` → `npm run tauri build`(或 `npm run tauri build` 直接触发 beforeBuildCommand)
   - [ ] 重装 /Applications/MYIA.app
   - [ ] cwd=`/` 跑包内 serve 二进制重演 prd.md 方法矩阵 → 全 OK(记录输出)
   - [ ] 实查 `<home>/myia.db`、`<home>/plugins/*.yaml` ≥4、`.seeded`
   - [ ] `open -a MYIA` 窗口存活 + 截图尽力留档

7. **收尾**
   - [ ] prd.md 验收逐条勾选(D7 口径修正同步到 prd 验收行)
   - [ ] spec 更新(desktop 打包/路径约定,若 spec 面涉及)
   - [ ] journal 记录;单 commit 提交

## 回滚点

每步独立可 revert;步骤 6 重装前旧 .app 先备份到 /tmp(冒烟失败可回退)。

## 审查门

- 步骤 1-5 完成后自查:PRD 验收逐条对照(尤其「仓库内开发行为不回退」)。
- 冒烟矩阵输出全文贴回 task notes,不裁剪。
