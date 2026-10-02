# myia-monitor — 变更监控(changedetection.io)

官方场景件,v1.1 分级 **remote(桌面可选)**:网页变更监控(watch 托管
变更 + REST API 拉取)。封装上游
[dgtlmoon/changedetection.io](https://github.com/dgtlmoon/changedetection.io)
(约 34.7k stars,Apache-2.0 License)。本包只含声明与文档,**不复制上游
代码**。

> v1.1 瘦身:MYIA 的**内置变更指纹**(ETag/Last-Modified/内容哈希 +
> L1-L4 降级链)已覆盖桌面主场景 —— 不装本插件,品类照常跑(铁律)。
> 本插件只是把 watch 托管、变更历史与推送决策交给一张自托管
> changedetection.io 实例的可选集成;本地 compose 已撤,实例部署走仓库
> `docker/plugins/myia-monitor/`(服务端可选)。

## 桌面路径(默认,零 Docker)

桌面默认路径**就是不用本插件**:品类 YAML 不写 `plugin:` 节,内置变更
指纹直接生效(`skip 原因=指纹未变` 是正常路径,见 `myia doctor --json`)。
需要集中 watch 管理与实例侧推送时,再接入已部署实例(下一节)。

## remote 模式(可选接入,零 Docker)

已有部署(本机、家庭服务器或云上)时,桌面端零 Docker 直接填地址:

```yaml
plugin:
  id: myia-monitor
  requires: []
  modes:
    remote:
      endpoint: https://my-monitor.example.com   # 换成你的实例地址
      token: keychain:myia/monitor/token
```

先写钥匙链再引用:

```bash
myia secret set myia/monitor/token     # 粘贴 API access key(UI Settings → API 开启)
```

- `provides: [changedetection]`:变更监控品类(`plugins/monitor.yaml`)
  经 L1 `direct_api` 调 `GET /api/v1/watch`(鉴权头 `X-Api-Key`)。
- 实例不可达/token 缺失 → 结构化 warning,品类照常跑(铁律)。

## 服务端形态(可选,不在桌面路径)

自托管 changedetection.io 实例是**服务端可选**部署:compose 在仓库
`docker/plugins/myia-monitor/`,桌面用户不需要它。从仓库根执行:

```bash
docker compose -f docker/plugins/myia-monitor/compose.yml up -d
open http://127.0.0.1:5000    # Web 控制台(UI 里 Settings → API 开启 access key)
```

- watch 数据在 `changedetection-data` 卷;`docker compose down` 不丢,
  `down -v` 才清。compose 零明文凭据。

## 凭据红线

- 本插件全部文件零明文凭据;token 只走 `keychain:myia/<scope>/<name>` 引用。
- 仓库即公开:实例地址只用 `example.com` 占位,真实地址零入库。

## 安装 / 移除

```bash
myia plugin install plugins/myia-monitor
myia plugin list --json
myia plugin remove myia-monitor
```
