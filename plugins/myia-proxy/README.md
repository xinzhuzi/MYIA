# myia-proxy — 代理池(proxy_pool)

官方场景件:给采集链提供可轮换的爬虫代理 IP 池。封装上游
[jhao104/proxy_pool](https://github.com/jhao104/proxy_pool)(约 23.7k stars,
MIT License)——定时抓取公开免费代理、验证入库、HTTP API 取用。
本包只含声明(`plugin.yaml`)、文档与 compose 薄封装,**不复制上游代码**。

## 能力

- `provides: [proxy_pool]`:品类 YAML 的 `sources[].proxy: pool:<name>`
  可指向本池(运输层接入随引擎任务排期推进;`proxy: direct` 始终可用)。
- 装不上/配置坏 → 核心流水线照常跑(铁律,见 `plugins/community/README.md`)。

## local 模式(docker compose)

```bash
myia plugin install plugins/myia-proxy   # 或在本目录直接执行下一行
docker compose up -d                     # 首次会从上游 git 构建镜像
curl "http://127.0.0.1:5010/get"         # 随机取一个可用代理 {"proxy": "..."}
curl "http://127.0.0.1:5010/get_all"     # 全量列表
curl "http://127.0.0.1:5010/count"       # 池内计数
```

- API 默认绑 `127.0.0.1:5010`(回环);对外暴露请自行加反向代理与鉴权。
- 池数据在 compose 卷/redis 容器内,`docker compose down` 即清。

## remote 模式(零 Docker)

已有一台部署好的 proxy_pool(或任何兼容 `/get`、`/get_all` 语义的代理池
API)时,不装 Docker,直接填地址:

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

## 凭据红线

- 本包 compose 零明文凭据;remote token 一律 `keychain:myia/<scope>/<name>`
  引用,先 `myia secret set myia/proxy/<name>` 写入钥匙链。
- 仓库即公开:任何真实代理地址、token 零入库。

## 安装 / 移除

```bash
myia plugin install plugins/myia-proxy    # 装入 ~/.myia/plugins(MYIA_PLUGIN_DIR 可覆盖)
myia plugin list --json
myia plugin remove myia-proxy
```
