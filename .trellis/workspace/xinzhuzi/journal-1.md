# Journal - xinzhuzi (Part 1)

> AI development session journal
> Started: 2026-10-01

---

## 2026-10-02 — 10-02-v11-plugins-source-arch 验收实跑落档

- AC#1 myia-osint 零 docker 真实侦察:`.venv/bin/python -m myia.cli osint https://example.com --json --timeout 240` → 退出码 0,status=success,vendor.commit 与 submodule pin 一致,子进程 Photon 侦察 3.4s 完成并产出结构化端点(internal/external/endpoints)。**验证通过 2026-10-02**
- AC#2 myia-proxy 轻量路径(进程内,零 Redis 零 docker):`.venv/bin/python -m myia.cli proxy --count 3 --timeout 8 --json` → 退出码 0,mode=in_process,status=success,3 个公开源全部抓取成功(约 2360 候选),测活 13 个、3 个可用(含延迟)。**验证通过 2026-10-02**

## 2026-10-03 v1.1.1 桌面数据通路修复(task 10-03-v111-desktop-paths)

- 按工单按图施工完成:entry.py `_serve_context()` 四级优先级(params>MYIA_HOME>.app 探测>dev cwd)+ 首跑种子(.seeded 幂等)+ health `first_run`;main.rs 注入 MYIA_HOME;tauri.conf resources 带官方四件套;feed 空态「运行第一个插件」CTA
- 施工中新发现并修掉:全新数据根 `<home>/plugins` 不存在时 health 仍报 plugins_dir(探查矩阵「无创建逻辑」的残余)→ 解析即建目录
- 验收实测:cwd=/ 全方法矩阵 OK(两条路都验:bundle 探测 + 真首跑 Rust 注入);种子四件套+db 落数据根;pytest 31+vitest 40 绿
- 口径修正(design.md D7):「plugins.list ≥4」不可达也不应达成 —— 官方插件是品类 YAML,plugins.list 是市场面;验收改为 health.plugins ≥4
- 遗留(非本任务):tests/test_baseline.py 一例预存失败(`ModuleNotFoundError: No module named 'tests'`,干净 main 复现);四件套 push 全引用 env:FEISHU_CHAT_ID,无凭据首跑=引导态(设计);截图像素级核对留主人
- Ruling: seed 判「目录无 *.yaml/*.yml」而非整目录空 —— 市场插件子目录不算品类,避免装了市场插件就拒绝种子官方件

## 2026-10-03 grill Round 1(主人×AI,决议全采纳,档=10-03-grill-v112)

- Q1 下一刀=发布工程先行(tag v1.1.1);Windows+crawl4ai L3+proxy_pool 排 v1.2(池档 10-03-v12-backlog)
- Q2 版本号对齐叙事:四处 0.1.0→1.1.1,tag v1.1.1,README 徽章 alpha→1.1;否决 0.2.0(与 v1.0 已交付叙事矛盾)
- Q3 开箱即有数据:随包第五件 myia-demo.yaml(真实免费源+stdout,零凭据),兼作 README 截图素材
- Q4 挂账处置:v10-release review→in_progress 重开(未勾验收移交 10-03-v111-release);crawl4ai/proxy-transport 注记 v0.2 已毕不重开
- Q5 test_baseline 预存失败:授权当场修(单独 commit)
- Q6 决议+执行项全部当轮落 trellis(3 子任务挂 grill-v112)
- Round 2 待主人批:R2-1 updater 三密钥生成+Secrets / R2-2 Apple 公证($99/年)买不买 / R2-3 PyPI 先 test.pypi 演练 / R2-4 四帖节奏与定稿人
- 事实修正:CI 非零起步(desktop-release.yml windows job 已在、pypi-publish.yml 手动 dispatch 已在);「发布零起步」旧口径作废

## 2026-10-03 桌面端 YAML 配置编辑器立项(task 10-03-yaml-editor,planning)

- 主人指示必须有 YAML 编辑模块;当轮探查落档(prd/design/implement 三件套齐,待拍板 5 项后 start)
- 探查要点:品类 YAML 是核心配置面,桌面五屏无查看/编辑入口;sidecar `_HANDLERS` 11 方法扩展点干净(sources.write 先例);校验同门 load_category、原子写先例 `_atomic_write_text` 均可复用
- 形态决议(推荐待批):独立第六屏「配置编辑」+ 源管理行「编辑」跳转;CodeMirror 6 做编辑器;myia-* 插件系统是数据源场景、无 UI 插件机制,编辑器以 UI 模块 + 协议扩展落地
- 顺带挖出地雷:sources.write 写回用 yaml.safe_dump(entry.py:687),**启停开关即抹掉品类 YAML 全部注释**(官方件大半是注释)——是否并入本任务修,列待拍板 1
- 关联普查档:C11(yaml.write 泛化缺失)由 yaml.save 收口;C13 增删改由原文编辑覆盖



- 主人点名两处不需要:侧栏「v1.1 骨架 · 业务接入见各屏空态」、顶栏「sidecar v0.1.0 · 协议 v1」→ 侧栏 footer 整块删;顶栏在线态只留绿点,版本/协议退 title 悬浮(排障不丢);connecting/error 态未被点名不动
- 验证:vitest 40/40 + tsc/vite build 绿(显式退出码);构建产物 grep 两处文案零残留、tooltip 模板仍在;tauri build 重装 /Applications(旧版备份 /tmp/MYIA.app.bak-hints-trim 可删);OCR 截屏目视:侧栏无骨架行、顶栏无版本字样,羊毛/显卡情报正常渲染
- 冒烟两个坑:①macOS 文件系统大小写不敏感,直跑 MacOS/MYIA 实际执行的是 sidecar(打 argparse help),看壳要看进程 myia-desktop;②System Events 窗口计数对后台窗口报 0,先 activate 再数
- 普查档 C10 文案半边由此收口(版本字段半边留档);spec 无涉(纯 UI 文案);单 commit

## 2026-10-03 grill Round 2/3(主人全按推荐;决议全文=10-03-grill-v112)

- Round 2 批复:R2-1 updater 做全(三密钥+UI「检查更新」接线入 v111-release,密钥=主人侧前置);R2-2 不买 Apple Developer,README 写右键打开指引;R2-3 PyPI 先 test.pypi 演练;R2-4 tag+PyPI 后发四帖(AI 素材/主人定稿)
- Round 3(trellis 文档补全):Q5 普查路由全批+排序修正(立即=推提交+docs 小修;tag 后=ci-gates/v1.1.2 批次/归档会话);Q6 B2/B3/B4=v1.1.1 改宣称、补实现排 v1.2(覆盖普查路由②的回炉);Q7 归档会话授权;Q8 task.py finish 跨会话防护(搭 docs-truth)
- 执行档全家福(全挂 grill-v112):v111-release(P0)/docs-truth(P1)/v112-desktop-batch(P1)/ci-gates/archive-review/v12-backlog(+B2/B3/B4)/test-baseline-import(已交付 review)
- 普查档转活清单(D3 ✅718d56c、B1 ✅定向、C10 ✅02a0dce、B5 ✅获批即推),状态归 review
- 并行会话协调:ui-hints-trim 已交付归档(02a0dce/49e223b),v111-release 的 C10 引用已修注

## 2026-10-03 普查路由落档:三修复任务定稿 + 普查档归档(落档会话)

- 依主人批准的普查路由(grill Round 3/Q5)落 3 个修复任务,草案经复核修订后定稿:`.trellis/tasks/10-03-docs-truth/`(A 组 docs 侧 pip 宣称 + env.example 键名 A3/A4 + task.py finish 跨会话防护 Q8;正式稿取代 07:50 占位稿)、`.trellis/tasks/10-03-v112-desktop-parity/`(B2–B4 补实现 + C 组全量,吸收 stub 10-03-v112-desktop-batch 与 v12-backlog 第 4 项,新目录落稿)、`.trellis/tasks/10-03-ci-gates/`(D1 rust-check + D2 ruff 最小门禁;取代占位稿);三档 context 双侧(implement/check)齐挂,validate 全绿
- 普查档 10-03-gap-census 验收第 4 条补注记(主人批准路由,3 任务已建)+ 活清单回标收口后归档至 `.trellis/tasks/archive/2026-10/`(commit f8664a6);归档顺修普查档 D4 行号 92-96→99-104
- 复核意见四条全吸收:①②回标不待开工——stub task.json 标 superseded→v112-desktop-parity、v12-backlog 第 4 项移交注记、v111-release 改口注记(README 文案改指 v1.1.2 批次,防 v1.1.1 发布时写成过期事实)均随建档 commit 完成;③ci-gates D4 证据改 build-sidecar.sh:99-104(pyinstaller `--specpath`,SPIKE_DIR 定义在 15 行,顺修草案 --specpass 笔误);④D3 验收改浮动基线口径(普查时点 1397/14,期间 v111-release/yaml-editor 移动总数,零失败为准)
- 落盘按现势增补(晚于草案的新拍板):C8/C9 已按 ui-feature-census grill Q1 划归 10-03-feed-ux——v112 正式档表格标注划出、验收不含,并记 C1×G1/G3 协议合参(PROTOCOL_VERSION 统一 +1);v112-desktop-parity 挂 grill-v112 子任务与执行树对齐
- journal 仍压着 yaml-editor 两行与 sidebar-brand-trim/ui-feature-census 两节未随行(其他会话内容,照旧不代提交);desktop/tests/.trellis/scripts 在途改动(Q8 防护施工中)一律未碰
