// MYIA 桌面壳:Tauri 2 窗口 + 持久 PyInstaller sidecar(stdin/stdout JSON-RPC)胶水。
// 协议权威定义:desktop/entry.py 模块注释 —— 请求行 {"id",method,params} →
// 应答行 {"id",result|{error:{code,path,message}}};无 id 的 {"type": …} 行是
// 流式事件(log/progress/completed),原样转发为 Tauri 事件 `sidecar://event`。
// 前端唯一入口:invoke("sidecar_request", { method, params })。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// 流式事件转发到前端所用的事件名。
const SIDECAR_EVENT: &str = "sidecar://event";
/// 壳层 sidecar 生命周期状态事件(C2 respawn;壳自发,非 sidecar 协议)。
/// 载荷 {state: "respawning"|"online"|"dead", attempt?, respawned?}。
const SIDECAR_STATE_EVENT: &str = "sidecar://state";
/// 单请求应答超时(run.start 立即返回;doctor 带代理探测时最重)。
const REQUEST_TIMEOUT: Duration = Duration::from_secs(120);
/// 自动 respawn 上限(超过转手动;退避序列 1/2/4/8/16s)。
const RESPAWN_MAX_ATTEMPTS: u32 = 5;
const RESPAWN_BASE_DELAY: Duration = Duration::from_secs(1);
/// respawn 后稳定存活该时长 → attempts 归零(下轮退避从头计)。
const RESPAWN_STABLE_AFTER: Duration = Duration::from_secs(10);

/// 第 n 次尝试的退避时长:1s·2^(n-1) → 1/2/4/8/16s(saturating,不溢出)。
fn backoff_delay(attempt: u32) -> Duration {
    RESPAWN_BASE_DELAY * 2u32.saturating_pow(attempt.saturating_sub(1))
}

struct Sidecar {
    child: Mutex<Option<CommandChild>>,
    /// id → 应答回递通道(pump 线程 demux 后回填)。
    pending: Mutex<HashMap<u64, tauri::async_runtime::Sender<Result<Value, Value>>>>,
    next_id: AtomicU64,
    /// 自动 respawn 已尝试次数(Terminated +1;稳定存活归零;手动拉起归零)。
    respawn_attempts: Mutex<u32>,
}

/// sidecar 常驻进程:`myia-core serve`,启动时 spawn,pump 任务独占消费其 stdout。
#[tauri::command]
async fn sidecar_request(
    state: State<'_, Sidecar>,
    method: String,
    params: Option<Value>,
) -> Result<Value, String> {
    let id = state.next_id.fetch_add(1, Ordering::SeqCst);
    let (tx, mut rx) = tauri::async_runtime::channel::<Result<Value, Value>>(1);
    state.pending.lock().unwrap().insert(id, tx);
    let request = json!({"id": id, "method": method, "params": params.unwrap_or(json!({}))});
    let write = {
        let mut guard = state.child.lock().unwrap();
        match guard.as_mut() {
            Some(child) => child.write(format!("{request}\n").as_bytes()).map_err(|e| e.to_string()),
            None => Err("sidecar 进程未运行".into()),
        }
    };
    if let Err(message) = write {
        state.pending.lock().unwrap().remove(&id);
        return Err(json!({"code": "sidecar_not_running", "path": "$", "message": message}).to_string());
    }
    match tokio::time::timeout(REQUEST_TIMEOUT, rx.recv()).await {
        Ok(Some(Ok(result))) => Ok(result),
        Ok(Some(Err(error))) => Err(error.to_string()), // 结构化错误对象原样给前端
        Ok(None) => Err(json!({"code": "sidecar_dropped", "path": "$",
            "message": "sidecar 应答通道关闭(进程可能已退出)"}).to_string()),
        Err(_) => {
            state.pending.lock().unwrap().remove(&id);
            Err(json!({"code": "sidecar_timeout", "path": "$",
                "message": "sidecar 应答超时(120s)"}).to_string())
        }
    }
}

/// stdout 单行分拣:有 type → 事件转发;有 id → 回递 pending 请求。
fn handle_stdout_line(app: &AppHandle, line: &str) {
    let parsed: Value = match serde_json::from_str(line) {
        Ok(value) => value,
        Err(e) => {
            eprintln!("sidecar stdout 非 JSON 行(忽略): {e}: {line}");
            return;
        }
    };
    if parsed.get("type").is_some() {
        let _ = app.emit(SIDECAR_EVENT, parsed);
        return;
    }
    if let Some(id) = parsed.get("id").and_then(Value::as_u64) {
        let state = app.state::<Sidecar>();
        let sender = state.pending.lock().unwrap().remove(&id);
        drop(state);
        if let Some(tx) = sender {
            let reply = match parsed.get("error") {
                Some(error) => Err(error.clone()),
                None => Ok(parsed.get("result").cloned().unwrap_or(Value::Null)),
            };
            let _ = tx.try_send(reply); // 容量 1,接收方未 recv 也进缓冲
        } else {
            // 迟到应答:请求已超时移除(sidecar_timeout 已回前端)。不留痕的
            // 静默丢弃会掩盖「壳超时 < sidecar 实际耗时」的配置问题,大声说出来。
            eprintln!("sidecar 迟到应答(请求已超时移除,丢弃) id={id} method={:?}",
                parsed.get("method").and_then(Value::as_str).unwrap_or("?"));
        }
    }
}

fn pump_task(app: AppHandle, mut rx: tauri::async_runtime::Receiver<CommandEvent>) {
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    handle_stdout_line(&app, &String::from_utf8_lossy(&bytes));
                }
                CommandEvent::Stderr(bytes) => {
                    eprintln!("sidecar stderr: {}", String::from_utf8_lossy(&bytes));
                }
                CommandEvent::Error(message) => eprintln!("sidecar 错误: {message}"),
                CommandEvent::Terminated(payload) => {
                    eprintln!("sidecar 退出: code={:?} signal={:?}", payload.code, payload.signal);
                    let state = app.state::<Sidecar>();
                    *state.child.lock().unwrap() = None;
                    // drop(child 关闭管道 = stdin EOF,冻结包 serve 循环干净退出;
                    // 遗留 python 孤儿靠此自清 —— design §2.1② 注记,冒烟核)
                    {
                        let mut pending = state.pending.lock().unwrap();
                        for (_, tx) in pending.drain() {
                            let _ = tx.try_send(Err(json!({"code": "sidecar_terminated", "path": "$",
                                "message": "sidecar 进程已退出"})));
                        }
                    }
                    drop(state);
                    schedule_respawn(app.clone());
                }
                _ => {}
            }
        }
    });
}

/// 拉起 sidecar(setup 首启 / 自动 respawn / 手动 sidecar_restart 三处共用)。
/// spawn 后即起 pump 任务独占消费其 stdout;MYIA_HOME 注入规则同 v1.1.1。
fn spawn_sidecar(app: &AppHandle) -> Result<CommandChild, Box<dyn std::error::Error>> {
    // MYIA_HOME 注入尊重用户显式设置(自动化/自定位数据根的逃生口):
    // 已设则原样继承,不夺权;未设才计算平台根并注入 + 预建目录。
    // sidecar 叫 myia-core:主程序 mainBinaryName=MYIA,macOS APFS 大小写
    // 不敏感,叫 myia 会在 Contents/MacOS/ 与 MYIA 撞名互相覆盖
    let mut command = app.shell().sidecar("myia-core")?.args(["serve"]); // entry.py RPC 模式;直通模式(无参数)留给 CLI 场景
    if std::env::var_os("MYIA_HOME").is_none() {
        command = command.env("MYIA_HOME", myia_home_dir(app)?);
    }
    // app/bundle 版本注入(C10):单一事实源 = tauri.conf.json 的 version
    // (package_info),sidecar `version` 应答透传为 app_version 字段;
    // 开发态缺省同源(与 .app 版本天然一致,无需另维护常量)。
    let app_version = app.package_info().version.to_string();
    command = command.env("MYIA_APP_VERSION", app_version);
    let (rx, child) = command.spawn()?;
    pump_task(app.clone(), rx);
    Ok(child)
}

/// 自动 respawn:指数退避(1/2/4/8/16s),超 5 次转手动(dead 态);spawn 失败
/// 按同序列自驱重试;稳定存活 10s 归零计数。锁内复核 child 仍空才 spawn,
/// 防与手动拉起双 spawn(C2)。
fn schedule_respawn(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        loop {
            let attempt = {
                let state = app.state::<Sidecar>();
                let mut guard = state.respawn_attempts.lock().unwrap();
                *guard += 1;
                *guard
            };
            if attempt > RESPAWN_MAX_ATTEMPTS {
                let _ = app.emit(
                    SIDECAR_STATE_EVENT,
                    json!({"state": "dead", "attempt": attempt - 1}),
                );
                eprintln!("desktop: sidecar 自动 respawn 超限(连续 {}/{RESPAWN_MAX_ATTEMPTS}),转手动拉起", attempt - 1);
                return;
            }
            let _ = app.emit(SIDECAR_STATE_EVENT, json!({"state": "respawning", "attempt": attempt}));
            eprintln!("desktop: sidecar 将在 {:?} 后自动 respawn(第 {attempt} 次)", backoff_delay(attempt));
            tokio::time::sleep(backoff_delay(attempt)).await;
            {
                let state = app.state::<Sidecar>();
                let mut child_guard = state.child.lock().unwrap();
                if child_guard.is_some() {
                    // 手动拉起已抢先复活,本任务退出防双 spawn
                    return;
                }
                match spawn_sidecar(&app) {
                    Ok(child) => *child_guard = Some(child),
                    Err(error) => {
                        eprintln!("desktop: sidecar respawn 失败(第 {attempt} 次): {error}");
                        continue; // attempts 已 +1,下一轮更长退避直至 dead
                    }
                }
            }
            let _ = app.emit(SIDECAR_STATE_EVENT, json!({"state": "online", "respawned": true}));
            eprintln!("desktop: sidecar 自动 respawn 成功(第 {attempt} 次)");
            // 稳定计时:存活满 10s → attempts 归零(又死则 Terminated 走更长退避)。
            // *guard == attempt 复核:稳定窗口内若又死(Terminated 已 +1)或手动
            // 拉起已归零,本任务不得抢先归零(防两代 respawn 任务交错重置)。
            tokio::time::sleep(RESPAWN_STABLE_AFTER).await;
            let state = app.state::<Sidecar>();
            let mut attempts_guard = state.respawn_attempts.lock().unwrap();
            if *attempts_guard == attempt && state.child.lock().unwrap().is_some() {
                *attempts_guard = 0;
            }
            return;
        }
    });
}

/// 手动拉起 sidecar(dead 态顶栏「拉起」按钮 / reprobe 升级链路;C2)。
/// 幂等:进程健在直接回 restarted=false,绝不杀活进程。
#[tauri::command]
async fn sidecar_restart(state: State<'_, Sidecar>, app: AppHandle) -> Result<Value, String> {
    {
        let mut child_guard = state.child.lock().unwrap();
        if child_guard.is_some() {
            return Ok(json!({"restarted": false}));
        }
        let child = spawn_sidecar(&app).map_err(|e| e.to_string())?;
        *child_guard = Some(child);
    }
    *state.respawn_attempts.lock().unwrap() = 0;
    let _ = app.emit(SIDECAR_STATE_EVENT, json!({"state": "online", "respawned": false}));
    Ok(json!({"restarted": true}))
}

/// MYIA 应用数据根(v1.1.1 桌面数据通路统一,与 entry.py `myia_home()` 同路径):
/// macOS `~/Library/Application Support/MYIA` / Windows `%APPDATA%\MYIA`
/// (按 Roaming 惯例拼,APPDATA 重定向的边缘形态由 Python 侧 APPDATA env 兜底)
/// / Linux `~/.myia`。spawn sidecar 时经 `MYIA_HOME` env 注入 —— 桌面上下文
/// 的路径解析一处定案;sidecar 自带 .app bundle 探测作双保险。
fn myia_home_dir(app: &AppHandle) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let home = app.path().home_dir()?;
    let dir = if cfg!(target_os = "macos") {
        home.join("Library/Application Support/MYIA")
    } else if cfg!(target_os = "windows") {
        home.join("AppData").join("Roaming").join("MYIA")
    } else {
        home.join(".myia")
    };
    std::fs::create_dir_all(&dir)?;
    Ok(dir)
}

/// tao 在 applicationDidFinishLaunching 无条件 activateIgnoringOtherApps(true)
/// (tao-0.37.1 app_state.rs:293,默认值出自 app_delegate.rs:106),连 `open -g`
/// 的后台启动语义都会被覆盖。窗口隐藏躲不开应用级自激活(键盘焦点仍被夺),
/// 只能在启动序列落定后把激活让回前一应用。
#[cfg(target_os = "macos")]
fn yield_focus_after_silent_start(app: AppHandle) {
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(500));
        let _ = app.run_on_main_thread(|| {
            use objc2::MainThreadMarker;
            use objc2_app_kit::NSApplication;
            if let Some(marker) = MainThreadMarker::new() {
                NSApplication::sharedApplication(marker).deactivate();
            }
        });
    });
}

/// 单实例锁(主人规则 2026-10-03:同项目只许一个实例)。macOS LaunchServices
/// 只防 bundle 重复打开,防不住 `open -n` / 直跑二进制 / dev 与 release 混跑;
/// 官方 single-instance 插件在 macOS 是空操作,故自持 flock。锁落数据根
/// (与 sidecar 的 MYIA_HOME 同规则):dev 构建与装机包共用默认根即互斥,
/// 验证流用 MYIA_HOME 沙箱时属独立实例域(沙箱=独立环境,合理)。
#[cfg(target_os = "macos")]
fn acquire_instance_lock() -> Option<std::fs::File> {
    use std::os::fd::AsRawFd;
    let root = if let Some(home) = std::env::var_os("MYIA_HOME") {
        PathBuf::from(home)
    } else {
        PathBuf::from(std::env::var_os("HOME")?).join("Library/Application Support/MYIA")
    };
    std::fs::create_dir_all(&root).ok()?;
    let file = std::fs::File::create(root.join(".instance.lock")).ok()?;
    let held = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0;
    held.then_some(file) // 进程退出由内核放锁,File 永不显式关闭(mem::forget 持有)
}

fn main() {
    let started = Instant::now();
    // 单实例门:已有实例持锁 → 转激活它并退出。激活须等本进程退干净再执行:
    // 两进程短暂共存同 bundle id 时 LaunchServices 可能选中将死的这个(实测踩过),
    // 故甩孤儿 shell 延时半秒后 open -b —— 既有实例经 Reopen 亮窗,不夺屏不弹窗。
    #[cfg(target_os = "macos")]
    match acquire_instance_lock() {
        Some(lock) => std::mem::forget(lock),
        None => {
            eprintln!("desktop: 已有 MYIA 实例在跑(单实例锁),转激活既有实例后退出");
            let _ = std::process::Command::new("/bin/sh")
                .args(["-c", "sleep 0.5; exec /usr/bin/open -b com.myia.app"])
                .spawn();
            return;
        }
    }
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        // updater:前端经 @tauri-apps/plugin-updater 检查/下载/安装;签名公钥见 tauri.conf.json
        .plugin(tauri_plugin_updater::Builder::new().build())
        // process:更新安装完成后的进程重启(relaunch)
        .plugin(tauri_plugin_process::init())
        // dialog:看图屏系统文件选择器(前端 @tauri-apps/plugin-dialog;权限见 capabilities/default.json)
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![sidecar_request, sidecar_restart])
        .setup(move |app| {
            // 冷启动打点(沿用 spike 惯例):进程启动 → sidecar spawn 完成。
            // 先 manage 后 spawn:pump 任务一启动就会触达 Sidecar 状态
            // (spawn_sidecar 内起 pump),manage 必须先行。
            app.manage(Sidecar {
                child: Mutex::new(None),
                pending: Mutex::new(HashMap::new()),
                next_id: AtomicU64::new(1),
                respawn_attempts: Mutex::new(0),
            });
            let child = spawn_sidecar(app.handle())?; // 首启失败仍硬失败(与现状一致;respawn 只管运行中退出)
            *app.state::<Sidecar>().child.lock().unwrap() = Some(child);
            // MYIA_SMOKE_ROUTE 静默冒烟钩子(v1.1.1 装机五屏截图用):launchctl
            // setenv 传入路由名(如 "feed"),启动即设 window.location.hash("#/feed");
            // 未设则零行为变化。立即 + 1500ms 两次 eval 兜底 webview 未就绪的窗口期,
            // 同值幂等;不 show 不 focus,静默启动语义不受影响。
            if let Ok(route) = std::env::var("MYIA_SMOKE_ROUTE") {
                let hash = if route.starts_with('#') {
                    route
                } else {
                    format!("#/{}", route.trim_start_matches('/'))
                };
                if let Some(win) = app.get_webview_window("main") {
                    let script = format!(
                        "window.location.hash = {}",
                        serde_json::to_string(&hash).unwrap_or_else(|_| "\"#/\"".into())
                    );
                    let _ = win.eval(&script);
                    let (win, script) = (win.clone(), script.clone());
                    tauri::async_runtime::spawn(async move {
                        tokio::time::sleep(Duration::from_millis(1500)).await;
                        let _ = win.eval(&script);
                        // 亮窗必须等 run loop 转起:setup 期 show() 的 orderFront 会被
                        // visible:false 的初始排序覆盖(实测 2026-10-03);Reopen 路径
                        // 能亮正是事件循环起来之后。同样仅冒烟 env 存在时触达。
                        let _ = win.show();
                    });
                }
            }
            // 静默启动(10-03-quiet-launch):主窗口 visible:false 出厂,Dock 点击
            // (RunEvent::Reopen)或对运行中实例再 open -a 才亮出。dev 构建与
            // MYIA_SHOW_ON_START=1 例外照旧启动即显示(open 不透传 shell env,
            // 发布包自动化验证须直跑二进制或 open 两次)。
            let show_on_start =
                cfg!(debug_assertions) || std::env::var_os("MYIA_SHOW_ON_START").is_some();
            if show_on_start {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            } else {
                #[cfg(target_os = "macos")]
                yield_focus_after_silent_start(app.handle().clone());
            }
            eprintln!("desktop: sidecar(serve) spawned in {} ms", started.elapsed().as_millis());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("tauri application failed to start");
    app.run(|app, event| {
        // Dock 图标点击 / 对运行中实例再 open -a:静默启动藏起的主窗口在此时亮出
        #[cfg(target_os = "macos")]
        if let tauri::RunEvent::Reopen {
            has_visible_windows: false,
            ..
        } = event
        {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 退避纯函数锁:序列恰为 1/2/4/8/16s(RESPAWN_MAX_ATTEMPTS 内),溢出安全。
    #[test]
    fn backoff_delay_doubles_per_attempt() {
        assert_eq!(backoff_delay(1), Duration::from_secs(1));
        assert_eq!(backoff_delay(2), Duration::from_secs(2));
        assert_eq!(backoff_delay(3), Duration::from_secs(4));
        assert_eq!(backoff_delay(4), Duration::from_secs(8));
        assert_eq!(backoff_delay(5), Duration::from_secs(16));
    }

    #[test]
    fn backoff_delay_saturates_without_panic() {
        // 极端 attempt(防御):saturating_pow 封顶,不 panic、单调不降
        assert!(backoff_delay(u32::MAX) >= backoff_delay(RESPAWN_MAX_ATTEMPTS));
        assert_eq!(backoff_delay(0), Duration::from_secs(1)); // 防御性下界
    }
}
