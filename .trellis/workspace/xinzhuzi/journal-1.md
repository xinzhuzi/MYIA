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
