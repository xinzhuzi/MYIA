# implement — v1.1.1 发布执行(有序清单,每步带验证)

> 依据 design.md(源选型/形状/接线点/注入顺序的实证细节均在彼处)。
> 顺序原则:先合代码与物料(步骤 0-6)→ 推 main(B5)→ 主人密钥就位 →
> 扫描(7)→ tag(8)→ 真机冒烟(9)→ PyPI(10)→ 素材(11)→ 收尾(12)。
> 每步验证命令在实施机上执行并留输出于任务日志(`.trellis/tasks/10-03-v111-release/`)。

---

## 步骤 0:基线审计(不改动)

- [ ] `git status --porcelain` + `git diff` 逐项过一遍在途改动
  (`dashboard-screen.tsx`、`tests/test_desktop_sidecar_protocol.py`、journal、
  `?? .trellis/tasks/10-03-yaml-editor/`)——并行会话工作,本任务不裹挟。
- [ ] 基线绿:
  ```bash
  uv run pytest -q          # 口径见 718d56c:uv run pytest -q(1397 passed, 14 skipped)
  cd desktop/ui-src && npm test && cd -    # vitest 全绿
  ```

## 步骤 1:demo 插件落盘(design.md §2 逐字落地)

- [ ] 写 `plugins/myia-demo.yaml`(内容 = design.md §2 代码块,零 `env:`/`keychain:`)。
- [ ] `desktop/src-tauri/tauri.conf.json` → `bundle.resources` 增
      `"../../plugins/myia-demo.yaml": "plugins/myia-demo.yaml"`。
- [ ] 验证(schema 校验 + 试抓 + 全链,全走真网络):
  ```bash
  uv run myia test plugins/myia-demo.yaml --json          # 试抓:items ≥ 10
  uv run myia run  plugins/myia-demo.yaml --json          # 全链:push stdout 出 JSON 行
  uv run myia doctor --json | head -40                    # plugins 目录健康,无 config_error
  grep -c 'env:\|keychain:' plugins/myia-demo.yaml        # → 0(铁律)
  ```

## 步骤 2:版本号四处 bump → 1.1.1(design.md §4)

- [ ] `pyproject.toml:7`、`myia-classifier/pyproject.toml:7`、
      `desktop/src-tauri/Cargo.toml:3`、`desktop/src-tauri/tauri.conf.json` `"version"`。
- [ ] `uv sync`(刷新 editable 元数据,`myia --version` 才会变);
      Cargo.lock 随任意 cargo 构建刷新,不手改。
- [ ] 验证:
  ```bash
  grep -n '^version' pyproject.toml myia-classifier/pyproject.toml   # 两处 1.1.1
  grep -n '"version"' desktop/src-tauri/tauri.conf.json              # 1.1.1
  grep -n -m1 '^version' desktop/src-tauri/Cargo.toml                # 1.1.1
  uv run myia --version                                             # myia 1.1.1
  (cd desktop/src-tauri && cargo check -q)                          # 刷 Cargo.lock
  grep -A1 'name = "myia-desktop"' desktop/src-tauri/Cargo.lock     # 1.1.1
  ```

## 步骤 3:updater UI「检查更新」接线(design.md §3)

- [ ] `cd desktop/ui-src && npm install @tauri-apps/plugin-updater@2 @tauri-apps/plugin-process@2`。
- [ ] settings 屏新增「软件更新」Card:检查更新 → `check()`;有更新显示
      version/notes → `downloadAndInstall()` → `relaunch()`;失败走 ErrorBox;
      无更新回显当前版本。main.rs / capabilities / Cargo.toml **勿动**。
- [ ] `settings-screen.test.tsx` 增三态用例(`vi.mock` 两插件模块,模式同
      settings.test.tsx:10-11)。
- [ ] 验证:
  ```bash
  cd desktop/ui-src
  npm test                       # vitest 全绿(含新用例)
  npm run build                  # tsc -b && vite build 过
  cd ../..
  ```
- [ ] 真机签名闭环(密钥就位后按 UPDATER.md §五 9.9.9 演示)——不阻塞步骤 4-6。

## 步骤 4:CHANGELOG.md 新建(design.md §5.1)

- [ ] keep-a-changelog 头 + `[1.1.1]`/`[1.1.0]`/`[1.0.0]` 三节,英文为主;
      1.1.1 必含桌面数据通路修复(MYIA_HOME 统一/随包插件/首跑种子,45639c3)。
- [ ] 验证:`grep -n '1.1.1\|MYIA_HOME\|first-run\|45639c3' CHANGELOG.md` 命中。

## 步骤 5:README 升格(design.md §5.2,中英两处同改)

- [ ] 徽章 :16 → `status-1.1`;alpha 段 :235/:500 改写;v1.1 行 :244/:509
      如实化 + 新增 v1.1.1 行;反馈闭环段 :118/:378 → 「随 v1.2 交付」;
      安装口径 :132/:393 改 `uv sync` 为主;:174 发布前先指向 Releases。
- [ ] 新增「下载安装」节:Releases dmg 链接 + 右键打开绕 Gatekeeper 三步指引。
- [ ] 验证:
  ```bash
  grep -n 'status-1.1\|右键\|Right-click\|v1.1.1' README.md
  grep -c 'status-alpha' README.md          # → 0
  ```

## 步骤 6:pypi-publish.yml 演练开关(R2-3,design.md §5.3)

- [ ] 增 `workflow_dispatch` 输入 `repository-url`(默认空 = 正式 PyPI);
      两个 Publish 步骤透传 `repository-url`;`docs/launch/RELEASE.md` 增
      「TestPyPI 演练」节。
- [ ] 验证(仅静态,不 dispatch):`python3 -c "import yaml;yaml.safe_load(open('.github/workflows/pypi-publish.yml'))"`
      与 `grep -n 'repository-url' .github/workflows/pypi-publish.yml docs/launch/RELEASE.md`。

## 步骤 7:提交 + 推送(B5:批后即推)

- [ ] 分批提交(建议:demo 插件+resources / 版本 bump / updater UI /
      CHANGELOG+README / pypi 演练开关),不含步骤 0 的在途异物。
- [ ] 验证:
  ```bash
  uv run pytest -q && (cd desktop/ui-src && npm test)   # 推前全绿门禁
  git push origin main
  gh run watch                                            # ci.yml 绿
  ```

## 步骤 8:主人侧硬前置 + 密钥扫描(顺序不可换)

- [ ] 确认主人已完成 R2-1:`npx tauri signer generate` + 3 个 Secrets
      (`TAURI_UPDATER_PUBKEY` / `TAURI_SIGNING_PRIVATE_KEY` /
      `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`,UPDATER.md §二)。未就位不打 tag。
- [ ] gitleaks 零命中:
  ```bash
  brew install gitleaks            # 本机实测未装;装不上→trufflehog→再不行 escalate
  gitleaks detect --source . --redact -v | tee ../10-03-v111-release/leaks-report.txt
  # 期望:报告零 finding;命令与摘要记任务日志
  ```

## 步骤 9:tag v1.1.1 → desktop-release → 真机冒烟

- [ ] `git tag v1.1.1 && git push origin v1.1.1`。
- [ ] 验证:
  ```bash
  gh run watch --workflow desktop-release.yml    # 绿;Windows job continue-on-error 可红
  gh release view v1.1.1 --json assets -q '.assets[].name'
  # 期望含:MYIA_1.1.1_aarch64.dmg / MYIA.app.tar.gz / MYIA.app.tar.gz.sig / latest.json
  gh release view v1.1.1 --json assets -q '.assets[] | select(.name=="latest.json")' 
  curl -sL https://github.com/xinzhuzi/MYIA/releases/latest/download/latest.json | head
  # latest.json 的 version=1.1.1,platforms 带 darwin-aarch64
  ```
- [ ] **真实安装冒烟**(spec/python/index.md:41 口径,mock 绿不算数):下载 dmg
      → 装 /Applications → 首跑点「运行第一个插件」→ feed 出 GitHub 新星真数据,
      不撞 config_error;`myia --version`(sidecar version 接口)与
      .app CFBundleShortVersionString 均 1.1.1:
  ```bash
  /usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' /Applications/MYIA.app/Contents/Info.plist
  ```
      冒烟记录(含 cwd=/ 矩阵结论)写入任务日志。

## 步骤 10:PyPI 双包(test.pypi 演练 → 正式,R2-3)

- [ ] 主人在 test.pypi.org 注册 publisher(或 test 侧 token,RELEASE.md 新节);
      dispatch `package=both` + `repository-url=https://test.pypi.org/legacy/`。
- [ ] 验证演练:
  ```bash
  python3 -m venv /tmp/tp && /tmp/tp/bin/pip install -i https://test.pypi.org/simple/ \
    myia==1.1.1 myia-classifier==1.1.1 && /tmp/tp/bin/myia --version
  ```
- [ ] 正式 dispatch(`package=both`,默认 repository-url),验证:
  ```bash
  python3 -m venv /tmp/pp && /tmp/pp/bin/pip install myia==1.1.1 myia-classifier==1.1.1 \
    && /tmp/pp/bin/myia --version && /tmp/pp/bin/python -c "from myia_classifier import keywords" 2>/dev/null || true
  # 链接(test + 正式)记任务日志;README :174 改实链(A2 兑现)
  ```

## 步骤 11:五屏截图 + 四帖素材(R2-4:AI 出素材,主人定稿)

- [ ] 用真机 demo 数据截五屏(dashboard/sources/feed/logs/settings)存
      `docs/demo/assets/`,README 引用(步骤 5 若早于本步,完成后回填)。
- [ ] `docs/launch/{v2ex,linuxdo,jike,reddit-r-selfhosted}.md` 四帖按 1.1.1 实况
      更新(dmg 链接/右键指引/PyPI 安装/新截图)。
- [ ] 验证:`git diff --stat docs/ docs/launch/`(截图与四帖有变更);素材交主人。

## 步骤 12:收尾

- [ ] 任务日志汇总:demo 冒烟记录、gitleaks 报告、Release/PyPI 链接、四帖定稿状态。
- [ ] PRD 验收清单逐项勾选;发帖后链接回填 `10-01-v10-release`(R2-4)。
- [ ] `python3 .trellis/scripts/task.py finish 10-03-v111-release`(按 trellis 流程收)。

---

## 禁区提醒(全程)

- 不改 `desktop/src-tauri/src/main.rs`(updater/process 已注册);不动
  capabilities/Cargo.toml 的 Rust 侧 updater 配置。
- tag 前主人三密钥未就位 → 停在步骤 8,escalate,不打 tag。
- gitleaks 非零命中 → 停,如实上报,不修不扫尾后继续。
