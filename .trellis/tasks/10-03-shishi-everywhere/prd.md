# 定名跟改:repo/PyPI/CLI 随世事更名 + 驱动 v1.1.1 发布

## Goal

主人令(2026-10-03「跟,发布」):①repo 名 MYIA→shishi(GitHub 重命名,旧 URL 自动重定向);②PyPI 发行名 myia→shishi、myia-classifier→shishi-classifier(名额已核实全空,均未发布过,零迁移);③CLI 脚本 myia→shishi;④README 31 处/docs 双语/发布帖模板/CHANGELOG/docker 镜像/updater endpoint 跟改;⑤tag v1.1.1 推送驱动 desktop-release 流水线。Python 模块目录 src/myia 改名刻意推迟(并行工作流在途,今晚树静后做,再做 PyPI 实发布——发行名 shishi 模块名 myia 的过渡态不进任何已发布版本)

## Requirements

模块改名(2026-10-04 树静后执行;repo/PyPI 发行名/CLI 键/README/docs 已由本任务前序件跟完):

1. **模块目录改名**:`git mv src/myia src/shishi`(保历史);全仓 import/引用跟改——src 自引用、tests、desktop/entry.py、plugins 适配器 docstring 的模块路径引用;动态 import 字符串、monkeypatch 目标串、`python -m myia.*` 子进程调用同跟。
2. **pyproject**:[project.scripts] 值 `myia.cli:main`→`shishi.cli:main`(键已是 shishi);[tool.hatch.build.targets.wheel] `packages=["src/myia"]`→`["src/shishi"]`;uv workspace(myia-classifier 成员,目录/模块名不变)关系核实不变;改后 `uv sync --all-extras` 重建环境(铁律)。
3. **打包链**:desktop/build-sidecar.sh PyInstaller 旗标(`--hidden-import myia.secrets`/`--collect-submodules myia`)与注释;desktop/myia-core.spec `hiddenimports`/`collect_submodules`;desktop/entry.py 顶部 import 与 run 子进程 `-m myia.cli`。**tauri 侧内部产物名不动**(binaries/myia-core-*、tauri.conf externalBin、mainBinaryName=MYIA、com.myia.app、MYIA_HOME)——守住爆炸半径,用户可见 CLI=shishi。
4. **PyPI 发布校验**:pypi-publish.yml 的 wheel/sdist 内容断言路径 `myia/…`/`src/myia/…`→`shishi/…`/`src/shishi/…`(文件头「模块改名后同步此处校验」注兑现);dispatch 输入值/项目键不变。
5. **Docker 运行链**:Dockerfile `ENTRYPOINT ["myia"]`→`["shishi"]`(CLI 脚本键已改 shishi,入口不跟则镜像破)及相关注释/示例;镜像名/系统用户 myia 不动。
6. **协议面零变更**:sidecar JSON-RPC 方法名/错误码/`PROTOCOL_VERSION` 不 bump(改名不是协议演进);仅 `_m_version` 应答 `name` 字段 myia→shishi,pytest/vitest 相应断言/mock 跟改。
7. **数据面标识符保持**(爆炸半径):`myia.db` 文件名、钥匙链 `SECRET_SERVICE="myia"` 与 `myia/<scope>/<name>` 命名空间、`MYIA_HOME` env、`~/Library/Application Support/MYIA`、`~/.myia`、UI localStorage 键 `myia.feed.states.v1`、sidecar 名 myia-core、myia-classifier/myia_classifier 包、`myia-*` 插件 ID、agent 技能名 myia(SKILL_DIR_NAME)。
8. **文档模块路径级引用跟改**:skill/SKILL.md、CONTRIBUTING、SECURITY、docker/{docker-compose.yml,env.example}、docs/{write-a-plugin.md,launch/RELEASE.md}、.trellis/spec 活档;纯名称营销文案与 CLI 命令例文(`myia run` 等)不动(前序件域);历史档(.trellis/tasks 归档、CHANGELOG 历史条目、docs/launch 草稿)不改写。

## Acceptance Criteria

- [x] grep 全仓 `(^|[^a-z_])myia(\.|$| import)` 类模块引用零残留(白名单:myia-classifier/myia_classifier、数据面标识符 `myia.db`/`myia.feed.states.v1`/Docker `USER myia`、历史档 `.trellis/tasks/**`、CHANGELOG 历史条目、docs/launch 草稿、git 历史)
- [x] `uv run --no-sync python -m pytest -q` 全绿(3044 passed, 19 skipped,2026-10-04 实跑)
- [x] `npm --prefix desktop/ui-src run test` 绿(262 passed,2026-10-04 实跑)
- [x] tauri 侧(desktop/src-tauri/**)与 sidecar 协议面零改动(git diff 核;仅 entry.py 的 name 字段一行例外)
- [ ] 全量门禁(含 tauri build)由编排脚本随后统一跑,本实现步不跑
- [ ] (主人门禁,保持未勾)PyPI 实发布 shishi/shishi-classifier——等主人配 trusted publisher 或 token+test 开关

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
