// MYIA 桌面 spike:Tauri 2 最小壳 —— 一个窗口 + 一段胶水调 PyInstaller sidecar。
// 胶水契约:`myia run <yaml> --json --db <临时库>`,stdout 原样回显前端。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::time::Instant;

use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::ShellExt;

/// 跑一次 sidecar 往返;yaml 为空则用 .app 内置夹具资源 spike.yaml。
#[tauri::command]
async fn run_pipeline(app: AppHandle, yaml: String) -> Result<String, String> {
    let started = Instant::now();
    let yaml_path = if yaml.trim().is_empty() {
        app.path()
            .resource_dir()
            .map_err(|e| e.to_string())?
            .join("spike.yaml")
            .display()
            .to_string()
    } else {
        yaml
    };
    let db = std::env::temp_dir().join(format!("myia-spike-ui-{}.db", std::process::id()));
    let spawned = app
        .shell()
        .sidecar("myia")
        .map_err(|e| e.to_string())?
        .args(["run", &yaml_path, "--json", "--db", &db.display().to_string()])
        .output()
        .await;
    let output = match spawned {
        Ok(output) => output,
        Err(e) => {
            eprintln!("spike: sidecar 调用失败: {e}");
            return Err(e.to_string());
        }
    };
    let stdout = String::from_utf8_lossy(&output.stdout);
    let summary = format!(
        "sidecar 往返:退出码 {} 耗时 {} ms stdout {} 字节",
        output.status.code().unwrap_or(-1),
        started.elapsed().as_millis(),
        output.stdout.len()
    );
    eprintln!("spike: {summary}");
    let json_head = stdout
        .lines()
        .rev()
        .find(|line| !line.trim().is_empty())
        .unwrap_or("(stdout 无非空行)")
        .chars()
        .take(160)
        .collect::<String>();
    eprintln!("spike: stdout 末行头 160 字符: {json_head}");
    let _ = app.emit("spike://run", &summary);
    Ok(format!("{summary}\n{stdout}"))
}

fn main() {
    let started = Instant::now();
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![run_pipeline])
        .setup(move |_app| {
            // 冷启动打点(实测数据用):从进程启动到窗口 setup 完成。
            eprintln!("spike: window ready in {} ms", started.elapsed().as_millis());
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("tauri application failed to start");
}
