# 桌面原型 spike 报告:Tauri 2 + PyInstaller sidecar

日期:2026-10-01 · 环境:macOS (darwin 25.4.0, arm64) · 工具链:cargo/rustc 1.95.0、node 25.9.0 / npm 11.12.1、Python 3.12.11(项目 venv)

## 一、书面结论

**继续 Tauri。** PRD 设定的切换条件(「Rust 胶水成本超预期 → 切 Flet」)未触发:

| 预期 | 实测 | 判定 |
|----|----|----|
| 安装包 <30MB 级 | .app **25 MiB**,dmg **20 MiB** | ✅ 达标 |
| 冷启动可用 | 壳窗口就绪 144–242 ms(5 次取样);壳内 sidecar 全链往返 305–324 ms | ✅ 达标(秒级以内) |
| Rust 胶水 ≤十几行 | sidecar 调用核心约 **15 行**;含错误日志/耗时打点/资源回退的 main.rs 全文 **72 行** | ✅ 成本低于预期 |
| sidecar 机制可行 | 官方 tauri-plugin-shell sidecar 路径全链验证:.app 内自动往返拿到 `"status": "success"` 的 RunResult JSON | ✅ 走通 |
| myia run <yaml> --json 契约 | 冻结二进制退出码 **0/1/2/3 全部实测正确**;`--json` stdout 为可 `json.loads` 的 RunResult | ✅ 完好 |

为什么不是 Flet:Flet 方案会把 UI 一并塞进 PyInstaller 包(体积只会更大)、放弃系统 WebView、窗口原生度差;而 Tauri 路线的全部 Rust 成本实测是 72 行胶水 + 9.3 MiB 壳二进制。退路保留:本 spike 未写任何桌面产品代码,Python 核心零改动,切 Flet 不影响核心的承诺依然成立。

## 二、实测数据(全部本机跑出,附命令)

### 2.1 体积

| 产物 | 体积 | 来源 |
|----|----|----|
| PyInstaller onefile sidecar(`desktop-spike/dist/myia`) | **16,263,136 B(15.5 MiB)** | `ls -l dist/myia` |
| Tauri 壳二进制(`Contents/MacOS/myia-desktop-spike`) | **9,761,248 B(9.3 MiB)** | `ls -l`(.app 内) |
| **MYIA.app 整包** | **25 MiB**(`du -h`) | `target/release/bundle/macos/MYIA.app` |
| **MYIA_0.1.0_aarch64.dmg** | **20 MiB** | `target/release/bundle/dmg/` |

.app 内两个主件:sidecar 15.5 MiB(与 dist/myia md5 一致:`2b1a63034b11dcce26e5ba31d36c7db4`)+ 壳 9.3 MiB ≈ 全部体积。Tauri 壳自身开销仅 ~9 MiB。

### 2.2 冷启动与往返耗时

| 项 | 数据 | 方法 |
|----|----|----|
| 壳窗口就绪(进程启动→Tauri setup) | **144 / 155 / 175 / 215 / 242 ms**(5 次启动取样,受磁盘缓存影响) | main.rs setup 钩子 `Instant` 打点,`Contents/MacOS/myia-desktop-spike` 终端直启,stderr 捕获 |
| 壳内 sidecar 全链往返(本 spike 主路径) | **324 ms / 305 ms / 307 ms**(3 次构建各测 1 次),退出码 0,stdout 3,375 B,末行 `"status": "success"` | UI 加载后自动 invoke,stderr 捕获(`spike: sidecar 往返:退出码 0 耗时 307 ms stdout 3375 字节`) |
| sidecar 独立进程往返(同一二进制直跑) | median **287 ms**、min 277 / max 292(n=7,每测独立临时 db) | python subprocess 计时 `myia run fixture/spike.yaml --json --db <tmp>` |
| sidecar `--version` | **214–244 ms**(n=5) | 同上。onefile 每次启动自解压,此为固有开销 |

结论:端到端「点按钮→看到 JSON」体感 <0.5 s;壳冷启动 <0.25 s。均远好于秒级预期。

### 2.3 Rust 胶水行数

- `desktop-spike/src-tauri/src/main.rs`:**72 行**(含注释与空行)。其中真正的 sidecar 调用核心(`app.shell().sidecar("myia")?.args([...]).output().await`)约 **15 行**;其余为空 yaml 回退到 .app 内置夹具资源、临时 db 路径、错误/耗时打点(为采证而加)。
- 前端 `desktop-spike/ui/index.html`:39 行免构建单页(`withGlobalTauri: true`,`window.__TAURI__.core.invoke`)。
- 配置面:`src-tauri/tauri.conf.json`(externalBin + resources 两项即完成 sidecar 嵌入与夹具内置)、`capabilities/default.json`(shell:allow-execute 一条)。

### 2.4 往返内容验证(sidecar 调 `myia run <yaml> --json`)

夹具:`desktop-spike/fixture/spike.yaml`(static_html 引擎,源指向 `http://127.0.0.1:8765/page.html` 本地静态服务,push 走 stdout 通道)——**零凭据、零外网**(127.0.0.1 为安全底线明示例外)。

- dev CLI 基线:`uv run --no-sync myia run ... --json` → exit 0,`status: success`,3 条目贯通 fetch/classify/dedup/analyze/push 五阶段。
- 冻结 sidecar 同夹具:exit 0,status success,items_out `[3,3,3,3,3]`——**与 dev CLI 行为一致**。
- 退出码契约(在冻结二进制上逐项触发):
  - 0 成功:`run fixture/spike.yaml` → **0** ✅
  - 1 配置错误:非法 cron 的 YAML → **1** ✅
  - 2 采集全部失败:源指向无监听端口 → **2** ✅
  - 3 部分失败:一好一坏两源 → **3** ✅
- .app 内往返(最终证据):`spike: stdout 末行头 160 字符: {"category": "spike-fixture", ..., "status": "success", ...}`

## 三、PyInstaller 打包要点(三个坑,均已解,复现在 `desktop-spike/build-sidecar.sh`)

1. **动态 import 漏收**:registry/push/classify 按字符串名动态 import 引擎与通道模块,PyInstaller 静态分析看不见 → 须 `--collect-submodules myia`。漏收症状:`No module named 'myia.engines.static_html'`,采集全失败退出码 2。
2. **`myia.secrets` 漏收(牵出仓库级 bug)**:`src/myia/schema.py:52` 的 `from myia import secrets` 与 stdlib `secrets` 同名,modulegraph 解析到 stdlib 而漏收 `myia/secrets.py` → 须 `--hidden-import myia.secrets`。更糟的是根因之一在仓库:`.gitignore:10` 的 `secrets.*` 把 **`src/myia/secrets.py` 整个排除在 git 之外**(`git check-ignore` 实证),hatchling 打 wheel 同步漏文件 → 已入 openIssues,须仓库级修复(gitignore 加例外),本 spike 只以可编辑安装绕过。
3. **onefile 启动开销**:每次启动自解压 ~235 ms。若 v1.1 正式版要再压冷启动,可改 `--onedir` 并把目录作为 Tauri resources 打进 .app(sidecar 机制不变)。

## 四、macOS 构建/分发工作量

- `npx tauri build`(本 spike 目录内 npm 本地安装 @tauri-apps/cli,未动全局):release 全量首次构建约 **8 分钟**(含 crates.io 网络抖动重试一次:首次因 crates.io 索引下载超时失败,重跑即成功);增量重建 **40–60 s**。target/ 中间产物 ~856 MB。
- 产物:`MYIA.app`(25 MiB)+ `MYIA_0.1.0_aarch64.dmg`(20 MiB);dmg 已实测挂载,含 MYIA.app + Applications 拖拽安装布局,推出正常。
- `open -n MYIA.app`(Finder 双击等价)启动成功、`osascript quit` 干净退出。未做签名/公证(ad-hoc;对外分发需 Apple Developer ID + notarytool,排 v1.1 正式版任务)。

## 五、边界与未测项(如实标注)

- **Windows 双端未实测**(无 Windows 机):Rust 壳层天然跨平台,sidecar 需在 Windows 上跑 PyInstaller 出 `myia-x86_64-pc-windows-msvc.exe`(外部Bin 按 target-triple 命名即被 Tauri 自动拾取),CI 用 matrix job——此为书面推断,标注「需主人手动验证」。
- **keyring/钥匙链在冻结环境未实测**:spike 夹具零凭据,未走 `myia secret set`/Keychain 读写路径(macOS 后端经 ctypes 调 Security.framework,打包后可用性待正式版验证)。
- macOS 通知/托盘/自启等桌面集成一律未做(spike 纪律:不铺开产品代码)。
- spike 期间 `desktop-spike/src-tauri/tauri.conf.json` 与 `src-tauri/icons/icon.icns` 于 22:47 被外部改动(productName/identifier/标题/图标改为 MYIA 品牌,非本 agent 所为,判断为主人或修复工程师);已按现状采用,两套产物尺寸均有记录(myia-spike 版:24.85/18.49 MiB;MYIA 版:25/20 MiB)。

## 六、验收标准对照(PRD)

- [x] macOS `.app`/dmg 打包成功、安装启动、sidecar 调用往返 —— MYIA.app 25 MiB + dmg 20 MiB,open 启动/退出、dmg 挂载、.app 内自动往返 status=success 全部实测
- [x] 实测数据(体积/耗时/胶水行数)记录在 research/ —— 即本文件第二节
- [x] 书面决策:继续 Tauri,依据见第一节(切换条件未触发)

## 七、复现指引

```bash
# sidecar(不动项目 .venv/pyproject/uv.lock,构建环境全部在 desktop-spike 内)
desktop-spike/build-sidecar.sh                       # → desktop-spike/dist/myia
python3 desktop-spike/fixture/serve.py 8765 &        # 本地夹具服务(127.0.0.1)
desktop-spike/dist/myia run desktop-spike/fixture/spike.yaml --json --db /tmp/t.db

# Tauri 壳(桌面 spike 目录内)
cd desktop-spike
cp dist/myia src-tauri/binaries/myia-aarch64-apple-darwin
npm install --no-fund --no-audit && npx tauri build  # → src-tauri/target/release/bundle/
```
