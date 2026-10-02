# myia-osint — OSINT 侦察爬虫(Photon)

官方场景件:对 watchlist 目标做一次性 OSINT 侦察爬取(站点 URL、邮箱、
社交账号、文件、密钥泄漏指纹等)。封装上游
[s0md3v/Photon](https://github.com/s0md3v/Photon)(约 13.2k stars,
**GPL-3.0 License**)。

> 许可边界:GPL 只作 **plugin 声明依赖 + 文档引用 + compose 拉上游源码**
> 三种方式接入;本仓库不复制、不修改、不分发 Photon 的任何源码。

## 能力

- `provides: [photon]`:侦察类品类(watchlist 驱动)可引用本插件产出的
  侦察结果文件作为补充信息源。
- Photon 是 **CLI 工具**(无服务形态),因此 local 模式 = 容器内跑一次爬取;
  remote 模式面向自建的 HTTP 包装层。
- 装不上不拦核心流水线(铁律)。

## local 模式(docker compose)

```bash
myia plugin install plugins/myia-osint
docker compose build photon                       # manifest 的 install 命令(首次拉上游源码)
docker compose run --rm photon -u https://example.com -o /Photon/loot
```

- `-u` 传目标,`-o` 指定输出目录(已挂载到宿主 `./loot/`);导出 JSON、
  ninja 模式等完整参数见上游 wiki(Usage)。
- 若 `-o` 行为与上游版本有出入,按上游 README 的挂载方式
  (`-v "$PWD:/Photon/<目标域名>"`)自行调整。

## remote 模式(零 Docker)

自建一层 HTTP 包装(把 Photon 跑成 API)后,品类 YAML 填:

```yaml
plugin:
  id: myia-osint
  modes:
    remote:
      endpoint: https://photon-wrapper.example.com   # 换成你的包装层地址
```

上游无鉴权;若你的包装层加了 token,token 走钥匙链引用
(`myia secret set myia/osint/<name>` 后在品类侧按
`keychain:myia/osint/<name>` 填写)。自检/doctor 只产结构化 warning,
不拦核心。

## 凭据红线

- compose 零明文凭据;仓库即公开,侦察目标只用公开域名做示例。

## 安装 / 移除

```bash
myia plugin install plugins/myia-osint
myia plugin list --json
myia plugin remove myia-osint
```
