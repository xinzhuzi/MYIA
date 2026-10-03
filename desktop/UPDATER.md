# MYIA 桌面 updater 运行手册(Runbook)

Tauri 2 官方 updater 插件的签名/发布/升级流程。**铁律:签名私钥与密码绝不入库**——仓库与 CI 日志里只允许出现公钥;真实值只经 GitHub Secrets / 本地环境变量传递。

## 一、当前接入点(代码位置)

| 位置 | 内容 |
|------|------|
| `desktop/src-tauri/tauri.conf.json` → `plugins.updater` | `endpoints`(默认指向 GitHub Releases 的 `latest.json`)、`pubkey`(**占位符** `__MYIA_UPDATER_PUBKEY_PLACEHOLDER__`,真实公钥由 CI 注入)、`windows.installMode: "passive"`(静默升级,仅进度条不点击) |
| `desktop/src-tauri/Cargo.toml` | `tauri-plugin-updater`(校验+下载+安装)、`tauri-plugin-process`(安装后 relaunch) |
| `desktop/src-tauri/src/main.rs` | 两个插件的 `.plugin(...)` 注册 |
| `desktop/src-tauri/capabilities/default.json` | `updater:default`、`process:allow-restart` |
| `.github/workflows/desktop-release.yml` | tag `v*` 触发;构建签名产物并附到 GitHub Release |

设计定案:`bundle.createUpdaterArtifacts` **不写进基础 conf**(否则本地 `npx tauri build` 会因缺 `TAURI_SIGNING_PRIVATE_KEY` 直接失败)。CI 发布时经 `tauri build --config <片>` 把真实 pubkey + `createUpdaterArtifacts: true` + 版本号一次性合并注入。因此:

- 本地普通构建(不打更新包):无需任何签名环境;
- 本地要出签名更新包:按第四节导出环境变量。

> **本地开发提示(10-03-ci-gates D5)**:`tauri dev` 前先在 desktop/ 下跑
> `bash build-sidecar.sh`。externalBin 指向被 gitignore 的 `binaries/`
> (tauri.conf.json `bundle.externalBin`),fresh clone 不先出 sidecar 则
> `tauri dev` 直接挂且无提示;sidecar 生成后不必每次重跑,改动 entry.py 才需要。

## 二、一次性配置:生成签名密钥并配 CI Secrets

在 desktop/ 内(npm 本地依赖,不动全局):

```bash
cd desktop
npx tauri signer generate -w ~/.tauri/myia.key
# 提示设置密码(可留空;留空则 CI 的 TAURI_SIGNING_PRIVATE_KEY_PASSWORD 也留空)
# 产出:
#   ~/.tauri/myia.key      私钥(绝不提交、绝不上传日志)
#   ~/.tauri/myia.key.pub  公钥(可入库/入 CI)
```

把公钥与私钥配进仓库 **Settings → Secrets and variables → Actions → New repository secret**(共 3 个):

| Secret 名 | 值 | 说明 |
|-----------|----|------|
| `TAURI_UPDATER_PUBKEY` | `myia.key.pub` 文件全文(一行 minisign 公钥) | CI 注入 tauri.conf 替换占位符 |
| `TAURI_SIGNING_PRIVATE_KEY` | `myia.key` 文件全文 | CI 签名更新产物 |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | 生成时设置的密码(未设则填空串/跳过) | 签名密码 |

验证公钥(可选):`cat ~/.tauri/myia.key.pub` 形如 `dW50cnVzdGVkIGNvbW1lbnQ6…` 的 base64 串。

## 三、发布流程(正常路径)

```bash
# 1) 本地确认桌面测试绿(全量 pytest 由门禁统一跑)
# 2) 打 tag 推送即触发 desktop-release.yml
git tag v0.2.0 && git push origin v0.2.0
```

流水线(macOS 主线):

1. `desktop/build-sidecar.sh aarch64-apple-darwin` 出 sidecar;
2. 写 `tauri.release.conf.json` 合并片(真实 pubkey + `createUpdaterArtifacts: true` + tag 版本号),`npx tauri build --bundles app,dmg --config tauri.release.conf.json`(**必须带 `app` 目标**:updater 的 tar.gz/.sig 只在 MacOsBundle 目标在列时产出,仅 `dmg` 会跳过更新包且随后删除 .app 中间产物,后续 `cat *.sig` 步骤必失败);
3. 产物上传 Release:**dmg**、**`世事.app.tar.gz` + `.sig`**(updater 增量包与签名)、**`latest.json`**(按 tag 生成)。

Windows job(msi)为**构建级验证**:`continue-on-error: true`——失败不阻塞 macOS 发布;成功时 msi + `.sig` 也附到 Release(不写 latest.json,Windows 条目见下文手工补)。Windows 实机升级交互标注**需主人**。

### latest.json 格式(updater 索引)

`endpoints` 指向的文件,放 Release 附件(固定名 `latest.json`,始终挂在**最新** release 上):

```json
{
  "version": "0.2.0",
  "notes": "MYIA v0.2.0:五界面正式版",
  "pub_date": "2026-10-02T12:00:00Z",
  "platforms": {
    "darwin-aarch64": {
      "signature": "<世事.app.tar.gz.sig 文件内容(一行)>",
      "url": "https://github.com/xinzhuzi/MYIA/releases/download/v0.2.0/世事.app.tar.gz"
    },
    "windows-x86_64": {
      "signature": "<MYIA_0.2.0_x64-setup.exe.sig 或 .msi.sig 内容>",
      "url": "https://github.com/xinzhuzi/MYIA/releases/download/v0.2.0/MYIA_0.2.0_x64-setup.exe"
    }
  }
}
```

规则:`version` 必须大于客户端当前版本才触发升级;`platforms` 键是 Tauri target triple(`darwin-aarch64` / `darwin-x86_64` / `windows-x86_64`),只列本次构建覆盖的平台,缺平台 = 该平台不提示更新;`signature` 是 `.sig` 文件的**内容**,不是文件本身。

## 四、本地出签名更新包(调试用)

```bash
cd desktop
export TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri/myia.key)"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="…"    # 未设密码则留空
npx tauri build --bundles app,dmg --config \
  <(printf '{"version":"9.9.9","bundle":{"createUpdaterArtifacts":true},"plugins":{"updater":{"pubkey":"%s"}}}' "$(cat ~/.tauri/myia.key.pub)")
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
- **Windows**:构建级 job 允许失败;WiX(mssi)/签名实机验证、passive 安装交互需主人于 Windows 实机确认;
- **密钥轮换**:pubkey 是信任根,换公钥 = 已发布客户端全部失去升级通道(需重装)。私钥疑似泄露时才轮换,且必须伴随一次人工通知;
- macOS 对外分发还需 Apple Developer ID 签名 + notarization 公证(与 updater 签名是两回事),未配置前下载 dmg 需右键绕过 Gatekeeper。
