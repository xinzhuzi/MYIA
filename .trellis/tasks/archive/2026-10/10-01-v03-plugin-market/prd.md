# 插件市场:plugin 双模式 + 目录

## Goal

场景件 plugin 化的基建(v1.7 消解桌面-Docker 矛盾):plugin.yaml 规范、local/remote 双模式、官方 6 插件、社区目录。铁律:**任何 plugin 装不上,核心流水线照常跑通**。

## Requirements

- plugin.yaml 规范:`id` / `requires` / `provides` / `modes.local`(docker compose)/ `modes.remote`(endpoint + keychain token)/ `install` / 版本矩阵(兼容 myia 版本范围)
- remote 模式:桌面用户零 Docker,填已部署服务地址+token(入钥匙链)
- 官方 6 插件:myia-proxy(proxy_pool)/ myia-osint(Photon)/ myia-douyin / myia-monitor(changedetection)/ myia-maxun / myia-credentials(aipocket)——各带独立 README + compose;核心仓库只装市场目录与加载器,重依赖全在 plugin 侧
- `plugins/community/` 目录规范 + 索引(社区发行文件,不在核心仓库托管实现)
- 装卸 CLI:`myia plugin list/install/remove`;加载器保证 plugin 失败不拦核心(启动自检+跳过+诊断)

## Acceptance Criteria

- [ ] myia-monitor 以 remote 模式在无 Docker 环境跑通
- [ ] 铁律测试:禁用/损坏 plugin,核心 run 无感
- [ ] plugin.yaml schema 校验 + 版本矩阵检查单测

## Notes

- aipocket 集成注意:其 API 为私有自部署(本机回环端口,JWT 鉴权)——插件配置走 endpoint+token 引用,不写死内网地址

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: AC2/AC3 有硬证据:跑 `uv run --no-sync python -m pytest tests/test_plugins_system.py tests/test_plugin_packages.py -q` → 161 passed, 1 skipped,含 TestIronLaw(插件未装/manifest 坏/remote 不可达 → run 退出码 0 仅 warning findings)、TestManifest/ TestVersionRange(schema fail-fast+版本矩阵);`uv run --no-sync myia plugin --help` 确认 list/install/remove 三子命令在;官方 6 插件 plugins/myia-{proxy,osint,douyin,monitor,maxun,credentials} 均有 README+plugin.yaml,docker/plugins/ 有 5 件部署目录(myia-credentials 为私有 aipocket,无本地部署形态,合 PRD note),plugins/community/ 有收录规范 README。

遗留(需主人手动完成):
- AC1「myia-monitor 以 remote 模式在无 Docker 环境跑通」仅有 mock 级证据(HTTP 500/超时/401-可达 均为 httpx.MockTransport);需主人以真实 changedetection.io 实例 endpoint + `myia secret set myia/monitor/token` 真实 token 实跑验证(真实凭据类,不代跑)
