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
