# myia-monitor — 变更监控(changedetection.io)

官方场景件:网页变更监控(watch 存储变更 + REST API 拉取)。封装上游
[dgtlmoon/changedetection.io](https://github.com/dgtlmoon/changedetection.io)
(约 34.7k stars,Apache-2.0 License)。本包只含声明、文档与 compose,
**不复制上游代码**。

## 能力

- `provides: [changedetection]`:变更监控品类的 watch 清单由它托管,
  myia 经 L1 `direct_api` 调 `GET /api/v1/watch`(鉴权头 `X-Api-Key`)。
- 双模式正是 v1.7 的官方示例:品类侧写法见
  [`plugins/monitor.yaml`](../monitor.yaml)(顶层 `plugin:` 节)。
- 装不上/不可达 → 核心照常跑:品类内置变更指纹 + L1-L4 降级链兜底(铁律)。

## local 模式(docker compose)

```bash
myia plugin install plugins/myia-monitor
docker compose up -d
open http://127.0.0.1:5000            # Web 控制台
```

- UI 里 Settings → API → 开启 API access key,记下该 key(下一步入钥匙链)。
- watch 数据在 `changedetection-data` 卷;`docker compose down` 不丢,
  `down -v` 才清。
- JS 渲染抓取为可选增强(见 compose 注释段)。

## remote 模式(零 Docker)

已有部署(本机、家庭服务器或云上)时,桌面端零 Docker 直接填地址:

```yaml
plugin:
  id: myia-monitor
  requires: docker
  modes:
    local:
      install: docker compose up -d
    remote:
      endpoint: https://my-monitor.example.com   # 换成你的实例地址
      token: keychain:myia/monitor/token
```

先写钥匙链再引用:

```bash
myia secret set myia/monitor/token     # 粘贴 API access key
```

## 凭据红线

- compose 与本包全部文件零明文凭据;token 只走
  `keychain:myia/<scope>/<name>` 引用。
- 仓库即公开:实例地址只用 `example.com` 占位,真实地址零入库。

## 安装 / 移除

```bash
myia plugin install plugins/myia-monitor
myia plugin list --json
myia plugin remove myia-monitor
```
