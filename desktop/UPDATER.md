# MYIA 桌面 updater 运行手册(Runbook)

Tauri 2 官方 updater 插件的签名/发布/升级流程。**铁律:签名私钥与密码绝不入库**——仓库与 CI 日志里只允许出现公钥;真实值只经 GitHub Secrets / 本地环境变量传递。

## 一、当前接入点(代码位置)

| 位置 | 内容 |
|------|------|
| `desktop/src-tauri/tauri.conf.json` → `plugins.updater` | `endpoints`(默认指向 GitHub Releases 的 `latest.json`)、`pubkey`(**占位符** `__MYSSIA_UPDATER_PUBKEY_PLACEHOLDER__`,真实公钥由 CI 注入)、`windows.installMode: "passive"`(静默升级,仅进度条不点击) |
| `desktop/src-tauri/Cargo.toml` | `tauri-plugin-updater`(校验+下载+安装)、`tauri-plugin-process`(安装后 relaunch) |
| `desktop/src-tauri/src/main.rs` | 两个插件的 `.plugin(...)` 注册 |
| `desktop/src-tauri/capabilities/default.json` | `updater:default`、`process:allow-restart` |
| `.github/workflows/desktop-release.yml` | tag `v*` 触发;构建签名产物并附到 GitHub Release |

设计定案:`bundle.createUpdaterArtifacts` **不写进基础 conf**(否则本地 `npx tauri build` 会因缺 `TAURI_SIGNING_PRIVATE_KEY` 直接失败)。CI 发布时经 `tauri build --config <片>` 把真实 pubkey + `createUpdaterArtifacts: true` + 版本号一次性合并注入。因此:

- 本地普通构建(不打更新包):无需任何签名环境;
- 本地要出签名更新包:按第四节导出环境变量。

> **本地开发提示(2026-10-05 更新,10-05-desktop-managed-py-env 第 7 步)**:
> PyInstaller sidecar 冻结链已退役——`tauri.conf.json` 已移除 `bundle.externalBin`,
> Python 面(源码/锁版清单/runtime manifest)改经 `bundle.resources` 随包,
> fresh clone 的 `tauri dev` / `npm run tauri build` 无需任何 Python 预构建;
> 运行时与依赖由用户首跑在设置页「开始配置」下载安装(自管环境,数据根拉起)。
> 旧链档注见 `desktop/build-sidecar.sh` 头注。

## 二、一次性配置:生成签名密钥并配 CI Secrets

在 desktop/ 内(npm 本地依赖,不动全局):

```bash
cd desktop
npx tauri signer generate -w ~/.tauri/myssia.key
# 提示设置密码(可留空;留空则 CI 的 TAURI_SIGNING_PRIVATE_KEY_PASSWORD 也留空)
# 产出:
#   ~/.tauri/myssia.key      私钥(绝不提交、绝不上传日志)
#   ~/.tauri/myssia.key.pub  公钥(可入库/入 CI)
```

把公钥与私钥配进仓库 **Settings → Secrets and variables → Actions → New repository secret**(共 3 个):

| Secret 名 | 值 | 说明 |
|-----------|----|------|
| `TAURI_UPDATER_PUBKEY` | `myssia.key.pub` 文件全文(一行 minisign 公钥) | CI 注入 tauri.conf 替换占位符 |
| `TAURI_SIGNING_PRIVATE_KEY` | `myssia.key` 文件全文 | CI 签名更新产物 |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | 生成时设置的密码(未设则填空串/跳过) | 签名密码 |

验证公钥(可选):`cat ~/.tauri/myssia.key.pub` 形如 `dW50cnVzdGVkIGNvbW1lbnQ6…` 的 base64 串。

## 三、发布流程(正常路径)

```bash
# 1) 本地确认桌面测试绿(全量 pytest 由门禁统一跑)
# 2) 打 tag 推送即触发 desktop-release.yml
git tag v0.2.0 && git push origin v0.2.0
```

流水线(macOS 主线 + Windows 正式目标 + release-finalize 归聚;2026-10-05 起无
Python 预构建步骤——sidecar 冻结链退役,Python 面经 tauri resources 随包):

1. 写 `tauri.release.conf.json` 合并片(真实 pubkey + `createUpdaterArtifacts: true` + 解析版本号),`npx tauri build --bundles app,dmg|msi --config tauri.release.conf.json`(**macOS 必须带 `app` 目标**:updater 的 tar.gz/.sig 只在 MacOsBundle 目标在列时产出,仅 `dmg` 会跳过更新包且随后删除 .app 中间产物,后续 `cat *.sig` 步骤必失败);
2. macOS job 产物上传 Release(仅 tag 触发):**dmg**、**`myssia.app.tar.gz` + `.sig`**(updater 增量包与签名,ASCII 名);
3. **release-finalize 归聚 job**(`needs: [macos-dmg, windows-msi]`,`if: always() && needs.macos-dmg.result == 'success'`):下载两平台产物 → 生成 **`latest.json`**(**单写者**:darwin-aarch64 条目恒在,windows-x86_64 条目按产物存在与否自动并入)→ Windows msi(`myssia_<版本>_x64.msi` + `.msi.sig`,ASCII 名)与 latest.json 挂 Release(仅 tag 触发)。`workflow_dispatch` 演练时不上传 Release,latest.json 以 `latest-json` artifact 交付复核。

Windows job(msi)为**正式交付目标**(10-04-windows-build):不再 `continue-on-error`;失败=run 红(可见),但不牵连 macOS 发布与 mac 更新通道(归聚 job 以 `always()` + mac result 门执行,windows 缺席仅表现为条目/资产缺席)。**手工补 windows 条目**降级为归聚失败的应急路径(见下文格式)。Windows 实机安装/升级冒烟清单(七项,主人侧)在 `10-04-windows-build` 任务档。

### latest.json 格式(updater 索引)

`endpoints` 指向的文件,放 Release 附件(固定名 `latest.json`,始终挂在**最新** release 上):

```json
{
  "version": "0.2.0",
  "notes": "世事 0.2.0",
  "pub_date": "2026-10-02T12:00:00Z",
  "platforms": {
    "darwin-aarch64": {
      "signature": "<myssia.app.tar.gz.sig 文件内容(一行)>",
      "url": "https://github.com/xinzhuzi/myia/releases/download/v0.2.0/myssia.app.tar.gz"
    },
    "windows-x86_64": {
      "signature": "<myssia_0.2.0_x64.msi.sig 内容>",
      "url": "https://github.com/xinzhuzi/myia/releases/download/v0.2.0/myssia_0.2.0_x64.msi"
    }
  }
}
```

规则:`version` 必须大于客户端当前版本才触发升级;`platforms` 键是 Tauri target triple(`darwin-aarch64` / `darwin-x86_64` / `windows-x86_64`),只列本次构建覆盖的平台,缺平台 = 该平台不提示更新;`signature` 是 `.sig` 文件的**内容**,不是文件本身。

## 四、本地出签名更新包(调试用)

```bash
cd desktop
export TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri/myssia.key)"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="…"    # 未设密码则留空
npx tauri build --bundles app,dmg --config \
  <(printf '{"version":"9.9.9","bundle":{"createUpdaterArtifacts":true},"plugins":{"updater":{"pubkey":"%s"}}}' "$(cat ~/.tauri/myssia.key.pub)")
# 产物:src-tauri/target/release/bundle/{dmg/*.dmg, macos/世事.app.tar.gz{,.sig}}
# (--bundles 必须含 app,理由见第三节)
```

版本故意给 `9.9.9` 只为本地自测;对外发布一律走 tag 流水线,版本号以 tag 为准。

## 五、演示「签名校验 + 静默升级」(PRD 验收,用测试更新源)

1. 本地按第四节构建一个 `9.9.9` 签名包,把 `世事.app.tar.gz`、`.sig`、手工 `latest.json` 放到一个可达的静态服务(如 GitHub 某个 draft/test release,或本地 `python3 -m http.server`);
2. 把正式版客户端(版本号较低)的更新源临时指到它:环境变量不可改 conf 时,直接在本地改 `tauri.conf.json` 的 `endpoints` 后重构建一个测试壳(**测试 conf 不提交**);
3. 客户端内检查更新 → 下载 → 校验签名 → passive 静默安装 → relaunch,版本变为 `9.9.9` 即演示完成;
4. 篡改实验(可选证明签名生效):改 `latest.json` 的 `signature` 任意一位,客户端应拒绝安装并报错。

## 六、缺口与需主人事项

- **Secrets 未配置前,tag 流水线会在守卫步骤直接中文报错**(不产出半成品 release)——需主人按第二节配置 3 个 secret;
- **前端检查更新入口未接线**:Rust 侧插件已注册,但 UI 调用需要 `ui-src` 增加 `@tauri-apps/plugin-updater` 与 `@tauri-apps/plugin-process` npm 依赖及设置页按钮(归设置界面实现方);
- **Windows**:正式交付目标(10-04-windows-build)——msi 以 ASCII 名挂 Release,latest.json windows 条目由归聚 job 自动并入;SmartScreen/Defender 放行口径见 README 安装节;WiX/签名实机验证、passive 安装交互仍需主人于 Windows 实机确认(冒烟清单七项在任务档);
- **密钥轮换**:pubkey 是信任根,换公钥 = 已发布客户端全部失去升级通道(需重装)。私钥疑似泄露时才轮换,且必须伴随一次人工通知;
- macOS 对外分发还需 Apple Developer ID 签名 + notarization 公证(与 updater 签名是两回事),未配置前下载 dmg 需右键绕过 Gatekeeper。

> **构建警示(2026-10-03 装机冒烟实测)**:任何**绕过 tauri CLI 的裸 `cargo build`**
> 都会产出零资产嵌入、烤死 devUrl 的白屏二进制(Cargo.toml 无 `[features]`
> custom-protocol 转发)。发版/验证一律走 `npm run tauri build`;根治方案
> (features 段转发)已建议入桌面对齐批次
> (10-03-v112-desktop-parity,随下个发布版节奏走)。
