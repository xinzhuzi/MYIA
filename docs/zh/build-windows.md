# Windows 桌面端本地构建(从源码到 MSI)

> 在你自己的 Windows 机器上,从源码打出 MYIA 桌面安装包(MSI)、装上、跑起来。
> 与 CI(`.github/workflows/desktop-release.yml` 的 `windows-msi` 作业)同一条构建链,
> 本文是真人机手册(2026-10 在一台全新 Windows 11 x64 机器全程实证)。
> Python 包( CLI / server)安装见[快速上手](getting-started.md),本文只讲桌面端。

## 0. 前置要求

| 依赖 | 安装 | 说明 |
|---|---|---|
| Windows 10/11 x64 | — | 磁盘空闲 ≥20GB |
| Git for Windows | [git-scm.com](https://git-scm.com/download/win) | 必须带 Git Bash(默认安装即含) |
| Node.js 20+ | [nodejs.org](https://nodejs.org/) | CI 用 22;24 实测可用 |
| uv | PowerShell:`irm https://astral.sh/uv/install.ps1 \| iex` | Python 侧构建入口,新开终端生效 |
| Rust(msvc) | `curl -L -o %TEMP%\rustup-init.exe https://win.rustup.rs/x86_64 && %TEMP%\rustup-init.exe -y --default-toolchain stable-x86_64-pc-windows-msvc` | 需 MSVC C++ 工具链:装过 Visual Studio(含 C++)或 VS Build With C++ 即可,rustup 自动探测复用;装完新开终端 `cargo --version` 验证,`rustc -vV` 的 `host:` 必须是 `x86_64-pc-windows-msvc` |
| WebView2 Runtime | Win11 一般自带 | 缺失时 MSI 安装会自动走 downloadBootstrapper 下载安装(需联网) |

网络:GitHub / npm / PyPI / crates.io 直连即可,无需代理与镜像。若你的 git 配了本地代理,见[坑 1](#常见坑速查)。

## 1. 克隆

```bat
git clone https://github.com/xinzhuzi/myia.git D:\dev\myia
```

任何 Git 客户端均可(图形客户端就用其自带 git 克隆)。

## 2. npm 依赖(两处)

```bat
cd /d D:\dev\myia\desktop && npm ci --no-fund --no-audit
cd /d D:\dev\myia\desktop\ui-src && npm ci --no-fund --no-audit
```

## 3. 构建 Python sidecar

sidecar 是 PyInstaller onefile 打包的 `myssia-core`(桌面端的 Python 引擎),必须在
Git Bash 里跑(脚本自带 Windows 适配):

```bat
"C:\Program Files\Git\bin\bash.exe" -c "cd /d/dev/myia/desktop && bash build-sidecar.sh x86_64-pc-windows-msvc"
```

- 首次 3–10 分钟:`uv sync` 从 PyPI 拉依赖(vision extra 含 onnxruntime 大 wheel);
- 产物:`desktop\src-tauri\binaries\myssia-core-x86_64-pc-windows-msvc.exe`(约 120MB);
- PyInstaller 阶段被 Windows Defender 首扫拖慢属正常,**等,不是失败**(见坑 4)。

## 4. 写 productName overlay(必做,躲 WiX 非 ASCII 雷)

```bat
echo {"productName": "myssia"} > D:\dev\myia\desktop\src-tauri\tauri.local.conf.json
```

**为什么必须**:基础配置的 `productName` 是「世事」(非 ASCII),WiX 链接器产不出
非 ASCII 产物名,不覆盖会在打包尾段报错。CI 用同一手法注入。该文件本地专用,
**不要提交入库**。

本地构建没有更新签名私钥(只存在于仓库 Secrets),按设计**不签名、不产更新包**,
不影响安装与使用——该 MSI 仅用于本地安装体验,不能用于 updater 升级验证。

## 5. Tauri 打包 MSI

```bat
cd /d D:\dev\myia\desktop
set "PATH=C:\Program Files\Git\bin;%PATH%"
set "MYIA_SIDECAR_SKIP=1"
npx tauri build --bundles msi --config src-tauri/tauri.local.conf.json
```

- `PATH` 前置 Git Bash 是**坑 3** 的解法(防 npm 脚本里的 `bash` 解析到 WSL 存根);
- `MYIA_SIDECAR_SKIP=1` 复用第 3 步产物,不重复打包 sidecar;
- 首次 Rust 全量编译约 5–20 分钟(WiX 由 tauri CLI 自动下载);
- 产物:`src-tauri\target\release\bundle\msi\myssia_<版本>_x64_en-US.msi`(约 122MiB)。

## 6. 安装

```bat
msiexec /i D:\dev\myia\desktop\src-tauri\target\release\bundle\msi\myssia_0.0.1_x64_en-US.msi /qn /norestart /L*v %TEMP%\myssia-install.log
echo %ERRORLEVEL%
```

退出码 `0`(或 `3010`=需重启)= 成功;也可直接双击 MSI 图形安装。默认装到
`C:\Program Files\myssia\`(含 `MYIA.exe` + `myssia-core.exe` + 插件)。

## 7. 启动与冒烟

**启动即亮窗(2026-10 起)**:Windows 构建(本地与 CI 同链)经平台覆盖件
`desktop/src-tauri/tauri.windows.conf.json`(主配置窗口项的全量镜像,`visible`
翻转为 `true`)合并出「窗口出厂即显示」——直接双击图标即见窗口,且 `focus:
false` 不抢前台焦点(任务栏闪烁提示);应用已在跑时再点图标,经
single-instance 插件回调聚焦既有主窗。直接启动即可:

```bat
start "" "C:\Program Files\myssia\MYIA.exe"
```

(历史注记)2026-10 之前主配置 `visible: false` 的静默启动漏到全平台,Windows
无 Dock/托盘唤出路径,双击图标会「看起来没打开」,彼时正路是带
`MYIA_SHOW_ON_START=1` 启动。该变量如今仍有效(强制启动即显示;焦点仅 dev
构建或显式 `MYIA_SMOKE_FOCUS=1` 才抢),主要服务于 macOS 发布包的静默启动
语义,Windows 侧已无需再设。如实注:「启动即亮窗」当前由覆盖件配置形状与
cargo 单测锚定,真机 MSI 双击验证随下个 tag 的 windows-msi 产物补做。

验证三点:任务管理器里 `MYIA.exe` 与 `myssia-core` 双进程存活;窗口渲染出仪表盘;
`%APPDATA%\MYIA` 数据目录生成。

(可选)想再深一层冒烟:打包前放一个最小品类(`config/smoke-local.yaml`,内容如下),
装好后仪表盘应列出该品类、数据目录生成 `myssia.db`,即证明 Python 引擎整链存活:

```yaml
id: smoke-local
name: 本地冒烟
schedule: "*/30 * * * *"
sources:
  - name: example-news
    engine: static_html
    url: "https://example.com/news"
    extract:
      type: list
      item: "article"
      fields:
        title: "h2 a"
        url: "h2 a@href"
push:
  - channel: stdout
```

## 常见坑速查

| # | 症状 | 原因 | 解法 |
|---|---|---|---|
| 1 | `git clone` 报 `Failed to connect to 127.0.0.1 port xxxx` | git 配了本地代理(`http.proxy` 或 **URL 级** `http."https://github.com/".proxy`)但代理进程没在跑 | 命令级空覆盖,不动机器配置:`git -c http.proxy= -c http.https://github.com/.proxy= clone …`。注意 **URL 级配置优先于全局,覆盖键必须同域**;或把你的代理开起来 |
| 2 | 打包尾段 WiX/`light.exe` 报错 | productName 非 ASCII | 第 4 步 overlay(必须) |
| 3 | `npm run sidecar` 打印 WSL 报错退出 | PATH 里的 `bash` 解析到 WSL 存根(机器未装发行版) | 命令级 `set "PATH=C:\Program Files\Git\bin;%PATH%"` 前置 Git Bash(第 5 步已含) |
| 4 | sidecar 打包卡几分钟 | PyInstaller onefile 被 Defender 首扫 | 等待即可,非失败 |
| 5 | 经 SSH/远程会话启动 MYIA 秒退(exit 0) | 无桌面的非交互上下文起不了 WebView2 | 在本地桌面会话跑;远程自动化用计划任务(`schtasks /create … /run`)在交互桌面会话里拉起(根因是 WebView2 起不来,与窗口显隐无关) |
| 6 | `curl -I https://crates.io/api/…` 403 | API 的 bot 拦截 HEAD 探测 | 无碍,cargo 实走稀疏索引,正常拉包 |

## 与 CI 的差异

Node 版本 CI 锁 22(本文 20+ 均可)/ CI 有 rust 缓存、真机首编全量 / CI 注入签名
密钥产更新包、本地按设计不签名 / 克隆方式任意。完整执行记录属维护方私有
工作档,不入公开仓。
