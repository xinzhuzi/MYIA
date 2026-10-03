# PRD:CI 门禁补全(src-tauri cargo check 编译门禁 + ruff 最小 lint 门禁)

## 背景

普查档(`.trellis/tasks/10-03-gap-census/prd.md`,本档落盘时已归档至
`.trellis/tasks/archive/2026-10/10-03-gap-census/`;第 4 节 D 组 + 第 6 节路由 4;
grill Round 3 拍板 tag 后开工)定位:`.github/workflows/ci.yml` 只有
pytest(`test`,ci.yml:10-23)与 vitest+tsc(`ui-test`,ci.yml:25-44)两个
job——**Rust(src-tauri)在 push/PR 上零编译检查**,要等 tag 触发的
desktop-release.yml 才第一次编译(普查档 D1/B1 行);Python 侧零 lint/类型门禁。
本任务给 push/PR 补两道轻门禁,顺手清两件卫生(D4/D5,可选)。

起草期实测基线(2026-10-03,本机 + /tmp 干净克隆,证据见各条):

- **fresh clone 上裸 `cargo check` 必红**:tauri-build 在 build.rs 里校验
  externalBin 存在性,报 `resource path 'binaries/myia-aarch64-apple-darwin'
  doesn't exist`。造空占位文件后复跑 `cargo check` 全绿(exit 0、零输出);
  frontendDist `../ui` 不入库但 dev 配置下不拦 check。**故 CI job 必须先造占位再
  check**(不必真跑 PyInstaller)。
- `uvx ruff check .`(ruff 0.16.10;仓库与父目录、用户级均无 ruff 配置)=
  **498 错**(I001×68、UP017×60、F401×47……,302 可自动修)——当前默认规则面
  远超官方文档最小集,直接上门禁会把轻任务变重;实测显式最小集
  `--select E9,F63,F7,F82` **0 错**(exit 0,零改动即可绿)。

## 需求(缺陷清单)

| # | 位置 | 问题 | 证据(起草期逐一核实) | 处理 |
|---|------|------|------|------|
| D1 | .github/workflows/ci.yml:9-44 | push/PR 对 desktop/src-tauri 零编译检查,PR 弄坏 Rust 代码 CI 依旧全绿 | ci.yml 全文仅两 job(本档核实);仅 tag 才编译=普查档 D1/B1 行;Cargo.toml:1-27(rust-version 1.77) | **核心**:新增 rust-check job |
| D2 | .github/workflows/ci.yml;pyproject.toml:37-51 | 零 lint 门禁:CI 无 ruff 步骤,pyproject 无 [tool.ruff],dev 组仅 pytest | pyproject.toml:38;实测 498 错/最小集 0 错(见背景) | **核心**:ruff 最小 select 进 CI+本地;mypy 仅评估 |
| D3 | tests/test_baseline.py:789 | 裸 `pytest` 假红(v111 implement.md 教的 `uv run pytest` 跑法必炸) | **已修**:718d56c 改回 `from conftest import` 并注明 prepend 语义;提交信息载明双跑法 1397 绿 | **不立项**;仅补防回归注记+回标普查档 |
| D4 | desktop/myia.spec:9 | PyInstaller 过期生成物入库,含机器绝对路径 `~/…/entry.py`;build-sidecar.sh:99-104 的 pyinstaller 调用 `--specpath "$SPIKE_DIR"`(SPIKE_DIR=脚本所在 desktop/ 目录,定义于 15 行)每次重新生成,入库副本纯噪声 | myia.spec:9;全仓 grep 除 .trellis 外零引用(本档核实) | 顺风车(可选):直接删 |
| D5 | desktop/src-tauri/tauri.conf.json:6-10,28 | externalBin 指向被 gitignore 的 binaries/(.gitignore:67),build 段只有 beforeBuildCommand 无 beforeDevCommand:fresh clone 不先跑 build-sidecar.sh 则 `tauri dev` 直接挂且无提示 | tauri.conf.json:28;desktop/package.json:7 已有 `sidecar` 脚本可挂 | 顺风车(可选):beforeDevCommand 或文档提示 |

### D1 实施口径

ci.yml 新增 job(如 `rust-check`):ubuntu-latest → rust toolchain → **先造占位**
`mkdir -p desktop/src-tauri/binaries && touch desktop/src-tauri/binaries/myia-$(rustc -vV | sed -n 's/^host: //p')`
→ `cargo check --locked`(working-directory: desktop/src-tauri)。push 与 PR 均触发。
若首跑 CI 与本地实测不符,以真实 CI 日志为准调整;不得放宽成 `|| true` 类永远绿的写法。

### D2 实施口径

- pyproject.toml 加 `[tool.ruff.lint] select = ["E9","F63","F7","F82"]`(起步集
  只拦语法错/未定义名等致命错,今日实测 0 错,零代码改动即可绿);
- CI 步骤用 `uvx ruff@<锁定版本> check .`(锁版本,防默认规则面漂移——起草期实测
  0.16.10 默认集就带出 498 错);dev 组视方案加 ruff,使本地 `uv run ruff check .`
  同款可跑;
- mypy **仅评估**:跑一次(如 `uv run --with mypy --no-sync mypy src/myia
  --ignore-missing-imports`)记录错误条数与结论进任务档;不进门禁、不修类型。

## 验收标准

- [ ] ci.yml 新增 rust-check job(push+PR 触发),含占位 externalBin 前置与
      `cargo check`;在本机干净克隆或试验分支注入一处 Rust 语法错可验其变红、
      撤错复绿(证明门禁真拦得住,不是永远绿)
- [ ] ci.yml 新增 ruff 门禁(push+PR 触发),显式最小 select + 锁版本;本地同款
      命令(写入任务档,如 `uv run ruff check .`)0 错
- [ ] **改完后 push 上 ci.yml 真实跑过一次全绿**:推 origin/main 后
      `gh run list --workflow=ci.yml` 可见该 commit 的 run 为 success,链接/输出贴
      任务档;若按流程走 PR,则 PR checks 全绿并在合并后的 push 上补一次绿 run 记录
- [ ] D3 防回归:tests/conftest.py 顶部(或 test_baseline.py:789 现注释旁)补一句
      「勿改回 `from tests.conftest import`,裸 pytest 会假红」;本地
      `uv run pytest -q` 与 `uv run python -m pytest -q` 双跑双绿并记录
      (以开工时实测基线为准——普查时点 1397 passed/14 skipped,期间
      v111-release/yaml-editor 会移动总数,以零失败为准)
- [ ] mypy 评估结论(错误条数 + 是否值得立项)写入任务档;若未跑,如实标注未跑
- [ ] (可选,D4)desktop/myia.spec 已删;`bash desktop/build-sidecar.sh` 正常走完、
      spec 随之重新生成,构建不受影响
- [ ] (可选,D5)择一落地:tauri.conf.json 加 beforeDevCommand(在任务档注明对
      dev 启动耗时的影响),或 desktop 文档(README/UPDATER.md 相应小节)写明
      「tauri dev 前先 `bash build-sidecar.sh`」
- [ ] 普查档活清单(已归档:`.trellis/tasks/archive/2026-10/10-03-gap-census/prd.md`
      文末注记)回标 D1/D2/D4/D5 状态;journal 记一笔

## 收口补记(2026-10-03 下午,接手会话;原实现偏离③④两笔在此补齐)

门禁本体(rust-check/ruff 两 job、ruff.toml、D3 注记、D5 文档)已随
fbba437/dee8e24/b4e2787/845f64a 落库;本补记只补证据与遗留项,零代码改动。

- **D1 三态实证**:①CI 检出无 binaries/(gitignore)——`rust-check` job 在
  全部近期 run 绿,含最新 37101247124(f814161)✓ 2m2s,**不误红成立**;
  ②本机 /tmp 干净克隆(HEAD=1f74878)按 ci.yml 同款造占位后
  `cargo check --locked` exit 0(18.69s);③同克隆 src/main.rs 注入未闭合
  定界符 → exit 101(`unclosed delimiter`),撤错复绿 exit 0——门禁真拦得住,
  非永远绿。
- **D2 同款核验**:本地 `uvx ruff@0.16.10 check .` 于**全工作树**(含并行
  会话在途未跟踪 .py)All checks passed exit 0;CI ruff job 同绿。
- **绿 run 证据**:run 37097686620(push 0a9f83f,2026-10-03T04:47:06Z,
  845f64a 之后)**四 job 全绿**(test/ui-test/rust-check/ruff)——验收
  「push 真跑一次全绿」达成。**注**:05:33:42Z 起(60022ff,crawl4ai L3
  批次)`test` job 连续红 10 run:该提交删 desktop/entry.py 的 image.* 与
  `_IMAGE_ACTIVE_JOB` 但未同步 tests/test_desktop_sidecar_protocol.py
  (fixture setattr 引用已删属性,setup 期 AttributeError);修复已在并行
  vision-pipeline 会话工作区在途(test 文件已改)。**红在 test job,两道
  新门禁在全部红 run 上依旧 ✓**——非 ci-gates 缺陷,归属他线收口。
- **D3 双跑双绿**(2026-10-03,本机):`uv run pytest -q` 与
  `uv run python -m pytest -q` 各 **1836 passed / 14 skipped / exit 0** 双绿
  (首对跑法曾现 crawl4ai×3/×1 瞬态红——当时并行会话正在改树,复跑两法
  连续全绿,单跑该文件 33/33 绿,判干扰型非跑法语义;两法全程零收集错/
  零 conftest 导入错,718d56c 语义在两法下等价);ci.yml test job 用
  `python -m pytest` 风格一致;README/docs 零 pytest 跑法字样,无口径漂移面。
- **mypy 评估(实跑)**:`uv run --with mypy --no-sync mypy src/myia
  --ignore-missing-imports` = **45 errors / 12 files / 58 checked**(exit 1);
  分布 arg-type×26、return-value×5、misc×5、union-attr×3、assignment×2、
  override/operator/list-item/call-overload 各 1;热点 store/sqlite.py×13、
  pipeline.py×7、cli.py×7。结论:全部为注解严格性形态,无一运行期 bug;
  清零+持续门禁是独立工程量(逐文件补注解),**不立项搭车**;若将来做,
  建议新文件先行渐进(per-file),不进 ci.yml(与本档「不做的事」一致)。
- **D4 以演化形态关闭**:原判「过期生成物直接删」被 845f64a 覆盖——
  myia.spec → **myia-core.spec**(sidecar 更名避 macOS APFS 与主程序
  MYIA 大小写撞名),且相对路径化(SPECPATH 推 _REPO_ROOT,全文件零
  绝对路径,任何 checkout 可复跑);入库副本由「含机器路径的噪声」变为
  「可复跑模板」,build-sidecar.sh:112-120 每次构建仍重新生成。不删。
- **D5 已落(文档路线)**:desktop/UPDATER.md:20-23「`tauri dev` 前先
  `bash build-sidecar.sh`」+ sidecar 生成后不必每次重跑;未加
  beforeDevCommand(dev 启动零额外耗时)。

## 不做的事(防蔓延)

- **不**清 498 条 ruff 存量、**不**扩规则集(I001/F401/UP 等另行立项)——起步集
  只拦致命错
- **不**加 mypy 门禁、不修任何类型错误(评估而已)
- **不**加 clippy/cargo fmt/Rust 测试门禁(cargo check 先行)
- **不**加 Windows/macOS Rust matrix(ubuntu 单平台先拦大头;跨平台构建仍归
  tag 发版流水线)
- **不**动 desktop-release.yml 与发版/版本策略(D6 属发布任务)
- **不**改任何产品代码与测试逻辑(D3 仅注记、D4 仅删生成物、D5 仅配置/文档)

## 关联

- 普查档:`.trellis/tasks/archive/2026-10/10-03-gap-census/prd.md` 第 4 节 D 组、
  第 6 节路由 4、文末拍板注记
- 风格参照:`.trellis/tasks/archive/2026-10/10-03-ui-hints-trim/prd.md`
