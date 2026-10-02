# myia-douyin — 抖音/TikTok 数据 API(Douyin_TikTok_Download_API)

官方场景件,v1.1 分级 **server-only(桌面默认集移出)**:自托管的抖音/
TikTok 数据 API(视频/作者/评论解析、无水印下载)。封装上游
[Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)
(约 20.4k stars,Apache-2.0 License)。本包只含声明与文档,**不复制上游
代码**。

> 为什么移出桌面默认集:上游是重 Web 服务(浏览器渲染 + 多容器栈),
> 不可进程化 —— 桌面路径(源码/进程内优先)不携带它。可用形态只有一种:
> **已部署实例按 remote 模式接入**(零 Docker);要不要自己部署一份,
> 见下文「服务端部署(可选)」。

## 桌面路径(remote 接入,零 Docker)

已有部署(本机或服务器)时,品类 YAML 填:

```yaml
plugin:
  id: myia-douyin
  modes:
    remote:
      endpoint: https://douyin-api.example.com   # 换成你的已部署地址
      token: keychain:myia/douyin/token          # 上游 X-API-Key
```

先写钥匙链再引用:

```bash
myia secret set myia/douyin/token      # 粘贴 dtk_... / API key
```

- `provides: [douyin_tiktok_api]`:短视频情报类品类经 L1 `direct_api`
  调其 REST API(`POST /api/v1/parse`,鉴权头 `X-API-Key`)。
- 装不上/不可达 → 结构化 warning,不拦核心流水线(铁律)。

## 服务端部署(可选)

**单容器快速通道**(V4 系 Docker Hub 镜像):compose 在仓库
`docker/plugins/myia-douyin/`,从仓库根执行:

```bash
docker compose -f docker/plugins/myia-douyin/compose.yml up -d
curl http://127.0.0.1:8000/docs    # API 文档
```

**上游 v5 主线**(默认分支,postgres+redis+api+worker 多容器栈,功能
完整:身份池/控制台/MCP):按上游文档 git clone 上游仓库后,在仓库根写
`.env`(`DTK_SECRET_KEY` / `POSTGRES_PASSWORD` / `REDIS_PASSWORD` /
`DTK_DATABASE_URL` / `DTK_REDIS_URL`,**全部自生成、零默认口令**),再:

```bash
docker compose -p dtk -f docker/compose.yml up -d --wait
```

v5 初始化管理员走容器日志里的一次性 setup token;程序调用在控制台
「API keys」页建 `dtk_` 前缀的 key(勾 `douyin:read` / `tiktok:read`)。

两条路线都默认只绑 `127.0.0.1`;对公网暴露需自行加 TLS 与鉴权(上游安全
文档有专章)。

## 凭据红线

- 本插件与 compose 全部文件零明文凭据;token 只走
  `keychain:myia/<scope>/<name>` 引用。
- 平台 cookie 等敏感凭据留在上游实例侧,不入 MYIA 仓库、不入品类 YAML。

## 安装 / 移除

```bash
myia plugin install plugins/myia-douyin
myia plugin list --json
myia plugin remove myia-douyin
```
