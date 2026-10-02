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

## 2026-10-03 桌面端清除开发期提示文案(task 10-03-ui-hints-trim)

- 主人点名两处不需要:侧栏「v1.1 骨架 · 业务接入见各屏空态」、顶栏「sidecar v0.1.0 · 协议 v1」→ 侧栏 footer 整块删;顶栏在线态只留绿点,版本/协议退 title 悬浮(排障不丢);connecting/error 态未被点名不动
- 验证:vitest 40/40 + tsc/vite build 绿(显式退出码);构建产物 grep 两处文案零残留、tooltip 模板仍在;tauri build 重装 /Applications(旧版备份 /tmp/MYIA.app.bak-hints-trim 可删);OCR 截屏目视:侧栏无骨架行、顶栏无版本字样,羊毛/显卡情报正常渲染
- 冒烟两个坑:①macOS 文件系统大小写不敏感,直跑 MacOS/MYIA 实际执行的是 sidecar(打 argparse help),看壳要看进程 myia-desktop;②System Events 窗口计数对后台窗口报 0,先 activate 再数
- 普查档 C10 文案半边由此收口(版本字段半边留档);spec 无涉(纯 UI 文案);单 commit
