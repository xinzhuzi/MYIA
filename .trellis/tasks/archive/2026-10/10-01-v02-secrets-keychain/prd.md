# 安全底座:钥匙链凭据三态

## Goal

桌面安全底线(规划 v1.7 补):凭据只进系统钥匙链,配置文件明文凭据 = 启动即报错。

## Requirements

- `keychain:` 引用实装:macOS Keychain(security 命令或 keyring 库)、Windows DPAPI(keyring);服务端/Linux 环境回退策略(design 定:env: 为主,secret 文件可选)
- 凭据引用三态齐备:`env:VAR` / `keychain:name` / CLI 首跑录入(`myia secret set <name>` 写入钥匙链)
- 明文检查扩展到所有凭据位(sources.headers、push.target、plugin remote token、enrich.api_key 等),命中即拒跑,错误信息指明字段路径
- `myia secret list/delete`;secret 名空间按用途分组(myia/<plugin>/<name>)

## Acceptance Criteria

- [ ] macOS 实机 set/get/delete/list 往返(手动验证记录)
- [ ] Windows DPAPI 代码路径 + 条件单测(CI 跳过)
- [ ] 全凭据位明文检查单测(每类凭据位一个样例)

## Notes

- fetch_base 的 `keychain:` 桩在本任务接通;keyring 库为唯一新增依赖(跨平台钥匙链抽象)

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: uv run --no-sync python -m pytest tests/test_secrets.py -q → 45 passed, 1 skipped;Windows DPAPI 条件单测=TestWindowsDPAPI(test_secrets.py:486,skip 原因『Windows DPAPI 代码路径,仅 Windows 实机可验』,符合 CI 跳过设计);全凭据位明文检查=TestLoadTimeCredentialPrefs 共 8 类样例(headers/post_body/engine_options/源扩展参数/push_target/enrich base_url/enrich api_key/plugin node token);set/get/delete/list 往返=TestSecretCrud+TestSecretList;CLI 三子命令见 src/myia/cli.py:355-377(secret set/list/delete)。

遗留(需主人手动完成):
- AC『macOS 实机 set/get/delete/list 往返(手动验证记录)』未做:任务目录无 macOS 实机验证记录文件,需主人在实机执行 myia secret set/get/list/delete 往返并记录。
- Windows DPAPI 实机验证需 Windows 机(单测按设计 CI 跳过)。
