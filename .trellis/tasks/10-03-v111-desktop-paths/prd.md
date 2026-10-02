# v1.1.1 桌面数据通路:应用数据根统一 + 插件随包 + 首跑种子(P0)

## 报障(2026-10-03 主人,已装 /Applications/MYIA.app 实测)

> 仪表盘数据不可用(sidecar 错误码 plugins_dir);情报流不可用(internal_error);请求失败:插件目录不存在: plugins

## 探查结论(2026-10-03 对已装二进制全方法探测,证据在案)

### 逐方法矩阵(cwd=/ 模拟 Finder 启动)

| 方法 | 结果 | 根因 |
|---|---|---|
| version | OK | — |
| health | **ERROR plugins_dir(相对 "plugins")** | cwd 依赖 |
| plugins.list | OK 但空,dir=~/.myia/plugins(**该目录不存在**) | 家目录策略,无种子 |
| store.items | **ERROR internal_error: unable to open database file** | db="myia.db" 相对 cwd(/ 下不可建) |
| logs.tail / secret.list / run.status | OK | 内存态/钥匙链 |
| doctor | **ERROR plugins_dir** | 同 health |
| run.start | invalid_params(需 yaml 路径) | UI 侧传参形态待对齐(修路径后复核) |
| sources.write | 校验正常 | 路径同上会踩同一坑 |

### 三个根因

1. **三套路径策略并存**:health/doctor 用相对 `plugins`;store.items 用相对 `myia.db`;plugins.list 用 `~/.myia/plugins`——同一 serve 二进制三种解析,互不一致
2. **cwd 依赖**:从仓库目录跑同一二进制 health 即 OK(plugins_dir=plugins 落在仓库)——桌面上下文 cwd=/ 全线断
3. **空壳发行**:.app Resources 只有 icon+夹具 plugin.yaml,**无随包官方插件、无首跑种子**;~/.myia 不存在也无创建逻辑 → 用户拿到的是无插件无库无数据的壳

### 附带教训(写入验收防复发)

v1.1 的 UI 测试全部 mock sidecar,未做真实 .app 启动冒烟 → 五屏"可用"只在 mock 层成立。本任务验收必须含**真实安装冒烟**。

## 修复设计(design 可细化)

1. **统一应用数据根 myia_home()**:macOS `~/Library/Application Support/MYIA` / Windows `%APPDATA%\\MYIA` / Linux `~/.myia`(现有 plugins.list 的 ~/.myia 引用收编至此);db/plugins 默认一律 <home>/myia.db、<home>/plugins
2. **serve 上下文解析**(优先级):显式 params > `MYIA_HOME` env(**Rust main.rs spawn sidecar 时注入**,一处) > bundle 探测(exe 位于 .app → 平台数据根) > 开发回退 cwd(仓库内运行行为不变)
3. **插件随包 + 首跑种子**:build 侧把官方插件(ai-news/wool/stocks/gpu-prices)收进 Resources/plugins/;serve 首跑发现 <home>/plugins 为空 → 从 bundle 拷贝可写副本(记录 seeded 标志)
4. **空态语义**:health 在零插件时返回 healthy+first_run 标志而非报错;UI 各屏空态引导(跑第一个插件)而非错误
5. run.start 的参数形态与 UI 对齐复核(sources 屏写回同)

## Acceptance Criteria

- [ ] 从 `/` cwd 直接跑已装二进制:health/plugins.list/store.items/doctor 全 OK,无一屏报错
- [ ] db 与 plugins 落在平台数据根(实查文件);首跑后 plugins.list ≥4 官方插件
- [ ] 仓库内开发行为不回退(cwd 回退语义有测试)
- [ ] **真实安装冒烟**:重装 .app → 五屏全部有数据或合理空态引导(截图/日志记录)
- [ ] 三种上下文解析优先级有单测;pytest+vitest 全绿
- [ ] 重打包装机验证

## Notes

- P0 发布阻断级(已装用户核心屏全断);涉及 desktop/entry.py、src-tauri/main.rs、build-sidecar.sh/tauri.conf resources、ui-src 空态
- 探查证据:2026-10-03 serve 全方法矩阵(本 PRD 表格即摘要)
