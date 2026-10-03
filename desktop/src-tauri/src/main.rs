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
/// 单请求应答超时(run.start 立即返回;doctor 带代理探测时最重)。
const REQUEST_TIMEOUT: Duration = Duration::from_secs(120);

struct Sidecar {
    child: Mutex<Option<CommandChild>>,
    /// id → 应答回递通道(pump 线程 demux 后回填)。
    pending: Mutex<HashMap<u64, tauri::async_runtime::Sender<Result<Value, Value>>>>,
    next_id: AtomicU64,
}

/// sidecar 常驻进程:`myia serve`,启动时 spawn,pump 任务独占消费其 stdout。
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
                    let mut pending = state.pending.lock().unwrap();
                    for (_, tx) in pending.drain() {
                        let _ = tx.try_send(Err(json!({"code": "sidecar_terminated", "path": "$",
                            "message": "sidecar 进程已退出"})));
                    }
                }
                _ => {}
            }
        }
    });
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

fn main() {
    let started = Instant::now();
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        // updater:前端经 @tauri-apps/plugin-updater 检查/下载/安装;签名公钥见 tauri.conf.json
        .plugin(tauri_plugin_updater::Builder::new().build())
        // process:更新安装完成后的进程重启(relaunch)
        .plugin(tauri_plugin_process::init())
        // dialog:看图屏系统文件选择器(前端 @tauri-apps/plugin-dialog;权限见 capabilities/default.json)
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![sidecar_request])
        .setup(move |app| {
            // 冷启动打点(沿用 spike 惯例):进程启动 → sidecar spawn 完成。
            // MYIA_HOME 注入尊重用户显式设置(自动化/自定位数据根的逃生口):
            // 已设则原样继承,不夺权;未设才计算平台根并注入 + 预建目录。
            let mut command = app.shell().sidecar("myia")?.args(["serve"]); // entry.py RPC 模式;直通模式(无参数)留给 CLI 场景
            if std::env::var_os("MYIA_HOME").is_none() {
                command = command.env("MYIA_HOME", myia_home_dir(app.handle())?);
            }
            let (rx, child) = command.spawn()?;
            app.manage(Sidecar {
                child: Mutex::new(Some(child)),
                pending: Mutex::new(HashMap::new()),
                next_id: AtomicU64::new(1),
            });
            pump_task(app.handle().clone(), rx);
            eprintln!("desktop: sidecar(serve) spawned in {} ms", started.elapsed().as_millis());
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("tauri application failed to start");
}
