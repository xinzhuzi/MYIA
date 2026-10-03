# v112-desktop-batch GUI 冒烟 runbook(主人执行;实现会话受静默纪律不代跑)

前置:工作树归静(并行批次落定)后,`npm --prefix desktop run tauri build`
重打包并安装(或先在现装 .app 上验,版本面差异不影响本两项)。

## C2:杀掉 sidecar 进程后自动 respawn 救回(prd 验收第 1 条)

1. 启动 MYIA .app,顶栏 sidecar 徽章应为「运行中」。
2. 找 sidecar pid 并杀:
   ```sh
   pgrep -f "myia-core serve"        # 记下 pid
   kill -9 <pid>
   ```
3. 预期(≤ ~1s 退避后):
   - 顶栏徽章闪「respawning」(sidecar://state 事件),随后回「运行中」;
   - 终端(eprintln,若从命令行起 .app 可见)出现
     `desktop: sidecar 将在 1s 后自动 respawn(第 1 次)` 与
     `desktop: sidecar 自动 respawn 成功(第 1 次)`;
   - 任意屏发起请求(如仪表盘刷新)不再得 `sidecar_not_running`。
4. 手动半边(dead 态拉起):连杀 5 次+触顶转 dead 后,顶栏「重新探测」
   应执行「探测 → sidecar_restart 拉起 → 再探测」恢复在线
   (use-sidecar-status.ts reprobe 升级链路)。
5. run 取消:情报流空态 CTA 或顶栏全局运行控件「跑一次」,run 进行中点 ✕
   → run.status 终态 `cancelled`,`pgrep -f "myia run"` 无残留。

## C3:重启 .app 后仪表盘仍见历史 run(prd 验收第 3 条)

1. 先有历史:跑过至少一次 run(C2 第 5 步之后自然满足),仪表盘「近期
   run 成功率」卡有数据。
2. 退出 .app(Cmd+Q)再启动。
3. 预期:仪表盘历史 run 列表仍在(runs.list 直读 SQLite runs 表,非内存
   注册表;重启即空 = 旧缺陷已修的对照)。

## 记录口径

两项各录一段截图/录屏 + 一句结论,回贴本文件下方即可关 prd 验收
第 1/3 条。
