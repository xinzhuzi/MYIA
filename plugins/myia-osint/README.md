# myia-osint — OSINT 侦察爬虫(Photon)

官方场景件,v1.1 分级 **desktop(桌面默认集)**,**v1.1 源码型插件首个
样板**:对目标做一次性 OSINT 侦察爬取(站点 URL、邮箱、社交账号、文件、
密钥泄漏指纹等)。封装上游
[s0md3v/Photon](https://github.com/s0md3v/Photon)(约 13.2k stars,
**GPL-3.0 License**)。

> 许可边界:GPL 上游只以 **git submodule 引用**接入(`vendor/Photon`,
> pin commit 见 plugin.yaml `vendor.pin`),本仓库不复制、不修改、不分发
> Photon 的任何源码;`adapter.py` 是 MYIA 侧适配器,非上游代码。

## 桌面路径(默认,零 Docker)

```bash
# 首次:初始化上游源码(submodule pin,可审计可升级)
git submodule update --init plugins/myia-osint/vendor/Photon

# 一次性侦察:结构化 JSON 输出;默认目标 example.com(合法演示域)
myia osint https://example.com --json
```

- 运行方式:`adapter.py` 以隔离子进程调用上游 CLI ——
  `uv run --no-project --with requests --with urllib3 --with tld python photon.py …`,
  依赖按需装进 uv 临时环境,**绝不进根依赖**;宿主需有
  [uv](https://docs.astral.sh/uv/)。
- 输出:单份结构化 JSON(`plugin/target/status/vendor(command/commit)/
  command/exit_code/duration_seconds/results/datasets`),`results` 即上游
  `-e json` 导出的十一类数据集(files/intel/robots/endpoints/keys/…)。
- 失败全部结构化(`vendor_missing` / `uv_missing` / `invalid_target` /
  `photon_failed` / `photon_timeout` / `photon_export_missing` /
  `photon_export_invalid`):环境与用法错误退 1,采集失败退 2。
- **装不上不拦核心流水线(铁律)**:vendor 缺失/uv 缺失只影响本命令,
  品类 run/doctor 照常(测试钉在 `tests/test_osint_plugin.py`)。
- 靶点纪律:只对**合法授权的公开目标**使用;默认目标 example.com 即演示用途。

## 服务端形态(可选,不在桌面路径)

Photon 是 CLI 工具(无服务形态);compose 在仓库 `docker/plugins/myia-osint/`,
直接从上游 git 仓库构建,本仓库不 vendored:

```bash
docker compose -f docker/plugins/myia-osint/compose.yml build photon
docker compose -f docker/plugins/myia-osint/compose.yml run --rm photon -u https://example.com -o /Photon/loot
```

自建一层 HTTP 包装(把 Photon 跑成 API)后,品类 YAML 填
`plugin.modes.remote.endpoint: https://photon-wrapper.example.com`
(换成你的包装层地址)。上游无鉴权;若你的包装层加了 token,走钥匙链引用
(`myia secret set myia/osint/<name>` 后按 `keychain:myia/osint/<name>` 填)。

## 凭据红线

compose 零明文凭据;仓库即公开,侦察目标只用公开域名做示例。

## 安装 / 移除

```bash
myia plugin install plugins/myia-osint
myia plugin list --json
myia plugin remove myia-osint
```
