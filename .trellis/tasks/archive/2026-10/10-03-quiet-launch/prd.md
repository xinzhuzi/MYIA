# 桌面端静默启动:打开 MYIA 不再抢前台焦点

## Goal

macOS 打开 MYIA(含 `open -g` 后台启动)必抢前台。改为静默启动:打开 = sidecar 后台跑起来,
主窗口默认不弹出不夺焦点;想看界面点 Dock 图标(或对运行中实例再 `open -a`)。
dev 构建(`tauri dev`)与 `MYIA_SHOW_ON_START=1` 例外,启动即显示,保住现有验证流。

## 报障与证据矩阵(2026-10-03 实测,v1.1.1 已装 /Applications/MYIA.app)

| 触发方式 | 现状(v1.1.1) | 期望 |
|---|---|---|
| `open -g -a MYIA`(后台启动) | **前台被夺**:frontmost process 变 MYIA(实测,见 evidence/baseline-open-g.txt) | 完全静默:无窗口、不夺前台 |
| `open -a MYIA` / Finder 双击(冷启动) | 窗口置前 + 键盘焦点被夺 | 同上静默;点 Dock 才显示 |
| 点 Dock 图标(运行中,窗口隐藏) | n/a(窗口总是可见) | 显示窗口 + 聚焦 |
| 对运行中实例再 `open -a MYIA` | activate 突到前台 | 显示窗口(等价 Dock 点击) |
| `tauri dev`(myia-desktop) | 窗口置前 | 保持启动即显示(冒烟/验证流依赖) |

附带观察:测试期间并行工作流的 `tauri dev` 实例(myia-desktop)同样把前台从 ZCode 抢走——
同根因,同修复覆盖(dev 例外除外,dev 场景开发者预期可见)。

## 根因(代码定位,tao-0.37.1 / tauri-2.12.1 / tauri-runtime-wry-2.12.1)

1. tao `app_state.rs:290-293`:`applicationDidFinishLaunching` 里**无条件**
   `NSApp.activateIgnoringOtherApps(true)`(默认值 `app_delegate.rs:106`);
   tauri-runtime-wry 建事件循环只定制了 Windows msg_hook / Linux app_id,**没暴露关闭口子**。
   连 `open -g` 的后台启动语义都被它覆盖 → 实测抢前台。
2. tao `window.rs:629-633`:窗口 visible+focused(均为默认)→ `makeKeyAndOrderFront`;
   `app_state.rs:432-454` window_activation_hack 对所有**可见**窗口启动时再置前一次。
   visible:false 的窗口会被 hack 跳过("Skipping activating invisible window")。

## Requirements

1. `tauri.conf.json` 主窗口 `visible:false` + `focus:false`。
2. `main.rs` setup:静默启动判定;dev 构建(`cfg!(debug_assertions)`)或 `MYIA_SHOW_ON_START`
   存在 → show+focus;否则 macOS 侧在启动落定后 `NSApp.deactivate()` 把激活让回前一应用
   (抵消根因 1 的无条件自激活,用 objc2-app-kit,版本已在锁内)。
3. `main.rs` 运行回调:`RunEvent::Reopen{has_visible_windows:false}`(Dock 点击/运行中再 open)
   → 主窗口 show + set_focus。macOS 专属事件,cfg 门控。
4. 不改 sidecar 协议、不改前端;MYIA 定位是后台情报中枢(push 到 IM),打开只为保活,
   界面按需亮出。

## Acceptance Criteria

- [x] 重装 release 包后 `open -g -a MYIA`:前台进程**不变**(实测保持 ZCode)、无窗口弹出、
      `myia-core serve` 在跑;`osascript quit` 干净退出。(evidence/acceptance-transcript.txt)
- [x] 运行中再 `open -a MYIA`:窗口显示并成为前台(Reopen 路径)。
- [ ] 点 Dock 图标:窗口显示 —— 与上同一 Reopen 回调,留主人自验(点 Dock 即可)。
- [ ] `npm run tauri dev`:窗口照常显示 —— `cfg!(debug_assertions)` 编译期恒真,未实跑
      (5173 端口被并行 dev 实例占用;按 os-etiquette 代理不拉 UI),主人跑 dev 即可目验。
- [x] `MYIA_SHOW_ON_START=1 /Applications/MYIA.app/Contents/MacOS/MYIA`:窗口显示、
      sidecar 212ms、quit 干净(发布包自动化验证逃生口;`open` 不透传 shell env,
      发布包验证须直跑二进制或二次 open)。
- [x] `cargo check` 绿;重打包 `npm run tauri build` exit 0(app 124.98MiB)。

## 已知取舍(记档,不另开任务)

- updater 装完 relaunch 后同样静默,想看界面点 Dock(与静默哲学一致)。
- Windows/Linux 不适用:产物仅 app/dmg;`visible:false` 跨平台生效但 Reopen/deactivate 均
  cfg 在 macOS,若未来扩平台需补显示路径。
- 发布包 UI 验证流(重装→open→OCR 截图)受影响:改为直跑二进制带 env,或 open 两次。
  后续 v112-desktop-parity 冒烟步骤落 implement 时要用新姿势。

## 验收记录(2026-10-03,受主人委托代验)

**verdict:conditional(归档;功能全过,余 2 项主人手动目验)**。

代码面全部核实(HEAD 已提交版):
- `desktop/src-tauri/tauri.conf.json` 主窗口 `visible:false` + `focus:false`;
- `desktop/src-tauri/src/main.rs:253-263` show_on_start = `cfg!(debug_assertions)` || `MYIA_SHOW_ON_START` → show+set_focus;否则 macOS 走 yield_focus_after_silent_start(main.rs:147-159,启动 500ms 后主线程 `NSApp.deactivate()`;objc2 0.6.4 / objc2-app-kit 0.3.2 已入 Cargo.lock);
- `RunEvent::Reopen{has_visible_windows:false}` → show+set_focus(main.rs:271-281,cfg 门控 macOS)。

AC 对照:evidence/acceptance-transcript.txt 覆盖 open -g 前台不变/无窗/sidecar 在跑/quit 干净、
运行中再 open -a 亮窗、MYIA_SHOW_ON_START=1 直跑二进制(sidecar 212ms)、cargo check/build
(转录;代验未重跑)。**余主人手动项**:①点 Dock 图标验证亮窗(与 open -a 同一 Reopen 回调,
点即目验);②`npm run tauri dev` 验证启动即显示(cfg!(debug_assertions) 编译期恒真,
跑 dev 即目验)。未发现应修缺陷。
