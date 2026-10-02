# myia-maxun — 无代码爬虫平台(Maxun)

官方场景件,v1.1 分级 **server-only(桌面默认集移出)**:浏览器录制式
无代码爬虫(recorder 机器人 → 结构化数据/REST API/定时任务)。封装上游
[getmaxun/maxun](https://github.com/getmaxun/maxun)(约 17.6k stars,
**AGPL-3.0 License**)。

> 许可边界:AGPL 上游只以 **manifest 声明依赖 + 文档引用**接入;本仓库
> 不复制、不修改、不分发 Maxun 的任何源码。
>
> 为什么移出桌面默认集:上游是 Electron 应用 + 多容器栈(postgres +
> minio + backend + frontend + browser),不可进程化 —— 桌面路径(源码/
> 进程内优先)不携带它。可用形态只有一种:**已部署实例按 remote 模式
> 接入**;要不要自己部署一份,见下文「服务端部署(可选)」。

## 桌面路径(remote 接入,零 Docker)

已有部署(自托管实例或上游托管版)时,品类 YAML 填:

```yaml
plugin:
  id: myia-maxun
  modes:
    remote:
      endpoint: https://maxun.example.com   # 换成你的实例地址
```

- `provides: [maxun]`:把任意站点录制成机器人,产出结构化数据;myia 侧
  经 L1 `direct_api` 消费其导出接口/REST 端点。
- 装不上/不可达 → 结构化 warning,不拦核心流水线(铁律)。

上游鉴权为控制台会话(浏览器登录),没有稳定的 API token 契约,故本插件
不声明 token;若你在实例前加了带 token 的网关,把 token 写入钥匙链
(`myia secret set myia/maxun/<name>`)后在品类侧按
`keychain:myia/maxun/<name>` 引用。

## 服务端部署(可选)

Maxun 是多容器栈(postgres + minio + backend + frontend + browser):
compose 在仓库 `docker/plugins/myia-maxun/`,从仓库根执行(与本目录配套
的 `.env` 也建在该目录,先从上游 `.env.example` 复制变量清单逐项填值,
口令自生成、零明文入库):

```bash
cd docker/plugins/myia-maxun
cp /path/to/upstream/maxun/.env.example .env   # 逐项填值(DB_USER/MINIO_*/BACKEND_URL/PUBLIC_URL…)
docker compose up -d
open http://127.0.0.1:5173      # 控制台,注册首个账号即管理员
```

- 与上游 compose 的差异:postgres/minio/browser 不发布宿主端口(走
  compose 内网),backend(8080)/frontend(5173)只绑回环 —— 更安全,
  行为一致。
- 数据在 `postgres_data` / `minio_data` 卷;`docker compose down` 不丢,
  `down -v` 才清。

## 凭据红线

- compose 零明文凭据:全部口令经 `${VAR:?}` 从 `.env` 注入;`.env` 不入库。
- 仓库即公开:实例地址只用 `example.com` 占位。

## 安装 / 移除

```bash
myia plugin install plugins/myia-maxun
myia plugin list --json
myia plugin remove myia-maxun
```
