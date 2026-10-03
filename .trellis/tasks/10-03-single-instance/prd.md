# MYIA 单实例:同数据根只许一个实例,重复启动激活既有实例

## Goal

主人规则(2026-10-03):「这个项目只能保持 1 个实例」。现状 macOS LaunchServices 只防
bundle 重复打开(open/Dock/Finder 双击天然去重),防不住三条漏网路:`open -n` 强开新实例、
直跑二进制、dev 构建(`tauri dev`/myia-desktop)与 release 装机包并存——当日实际发生:
myia-desktop dev 实例与 MYIA 装机包同屏并存,主人截图报障。

## Requirements

1. 数据根(与 sidecar 的 `MYIA_HOME` 同规则:显式 env > macOS
   `~/Library/Application Support/MYIA`)下 `.instance.lock`,启动即 `flock(LOCK_EX|LOCK_NB)`。
2. 拿不到锁 = 已有实例:执行 `open -b com.myia.app`(激活既有实例,既有实例经
   RunEvent::Reopen 亮窗),本进程 `return` 退出。不夺屏、不报弹窗。
3. 拿到锁:`std::mem::forget` 持有至进程退出,内核自动放锁(崩溃也放)。
4. `MYIA_HOME` 沙箱 = 独立实例域(验证流隔离是特性不是漏洞,记档)。
5. 为什么不用 tauri-plugin-single-instance:该插件 macOS 分支是空操作(LaunchServices
   已去重的场景它不管,dev/release 混跑它也管不了),自持 flock 是唯一覆盖 macOS 全部
   双开路径的做法。
6. 只 cfg 在 macOS(产物仅 app/dmg);dev 与 release 默认共用数据根,互斥天然成立。

## Acceptance Criteria

- [x] 首启 `open -g -a MYIA`:静默在跑(持锁),前台不变。
- [x] `open -n -a MYIA`(强制新实例):新进程秒退,既有实例被激活亮窗(Reopen 路径),
      全程恰好一个 MYIA 应用进程。首版当场 `open -b` 有 LaunchServices 竞态(激活到
      将死的第二实例,实测窗口不亮),改孤儿 shell `sleep 0.5` 后激活,复测过
      (窗口 0→1、frontmost=MYIA)。
- [x] 直跑二进制二次启动:打印「已有 MYIA 实例在跑」秒退,仍 1 进程。
- [x] `osascript quit` 后锁释放,可再次正常启动(sidecar ×2 随起,onefile 双进程正常)。
- [x] `cargo check` 绿;`npm run tauri build` exit 0;重装 /Applications。
- [x] sidecar(myia-core)不受影响:不经此锁,随应用生命周期。

## 已知取舍

- tauri dev 重启(kill 旧→起新)有毫秒级锁释放窗口,内核在进程死亡时同步放锁,
  实际竞态概率≈0;工作流 dev 循环若偶发秒退,重试即过(记给后续桌面验证流)。
- 更新器 relaunch:旧进程先退出放锁,新进程正常接管,无死锁面。
