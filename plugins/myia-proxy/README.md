# myia-proxy — 代理池(proxy_pool)

官方场景件,v1.1 分级 **desktop(桌面默认集)**:给采集链提供可轮换的爬虫
代理 IP。上游 [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool)
(约 23.7k stars,MIT License)——本插件**不复制、不 vendored 上游任何源码**,
`adapter.py` 是 MYIA 侧代码,参照其「fetch → 校验 → 取用」思路自实现的
进程内精简版。

## 桌面路径(默认,零 Docker 零 Redis)

```bash
myia proxy --json          # 一次抓取公开免费代理 + 逐个测活,结构化输出
myia proxy --count 10      # 期望可用数凑 10 个(凑够即提前停)
myia proxy --timeout 15    # 单代理测活超时(秒)
```

- 流程:逐源礼貌抓取公开免费代理列表(纯文本 `ip:port`,失败互相隔离)→
  去重入池(有界:至多测活 60 个)→ 逐个经代理请求校验目标,状态 `<400`
  记可用并记延迟。
- 输出:单份结构化 JSON(`plugin/mode/status/requested_count/sources(逐源
  报告)/fetched/checked/alive(proxy+latency_ms)/duration_seconds`)。
- 失败全部结构化:`invalid_count` / `invalid_timeout`(用法错误,退 1)、
  `fetch_failed`(全部源抓取失败,退 2)、`no_alive_proxy`(抓到候选但测活
  零可用,退 2)。
- **装不上不拦核心流水线(铁律)**:适配器缺失/抓取失败只影响本命令,品类
  run/doctor 照常(测试钉在 `tests/test_proxy_plugin.py`)。
- 诚实边界:免费公开代理质量不稳、寿命短,本路径适合应急与演示;生产采集
  用付费代理,或部署完整 proxy_pool(见下)后按 remote 接入。

## remote 模式(零 Docker)

已有一台部署好的 proxy_pool(或任何兼容 `/get`、`/get_all` 语义的代理池
API)时,直接填地址:

1. 品类 YAML 顶层声明(参考写法):

   ```yaml
   plugin:
     id: myia-proxy
     modes:
       remote:
         endpoint: https://proxy-pool.example.com   # 换成你的已部署地址
   ```

2. `myia doctor --json` / run 自检会给出结构化 warning(端点不可达等),
   不拦核心。

上游 proxy_pool 本身无 API 鉴权;若你在前面加了带 token 的网关,把 token
写入钥匙链后按 `keychain:myia/<scope>/<name>` 引用(见下节红线)。

## 服务端形态(可选,不在桌面路径)

完整 proxy_pool 服务形态(定时抓取 + Redis 池 + HTTP API `127.0.0.1:5010`)
是**服务端可选**部署:compose 在仓库 `docker/plugins/myia-proxy/`,桌面用户
不需要它。从仓库根执行:

```bash
docker compose -f docker/plugins/myia-proxy/compose.yml up -d
curl "http://127.0.0.1:5010/get"    # 随机取一个可用代理 {"proxy": "..."}
```

- API 默认绑回环;对外暴露请自行加反向代理与鉴权。compose 零明文凭据。

## 凭据红线

- 本插件全部文件零明文凭据;remote token 一律 `keychain:myia/<scope>/<name>`
  引用,先 `myia secret set myia/proxy/<name>` 写入钥匙链。
- 仓库即公开:任何真实代理地址、token 零入库。

## 安装 / 移除

```bash
myia plugin install plugins/myia-proxy    # 装入 ~/.myia/plugins(MYIA_PLUGIN_DIR 可覆盖)
myia plugin list --json
myia plugin remove myia-proxy
```
