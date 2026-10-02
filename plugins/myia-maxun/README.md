# myia-maxun — 无代码爬虫平台(Maxun)

官方场景件:浏览器录制式无代码爬虫(recorder 机器人 → 结构化数据/REST
API/定时任务)。封装上游 [getmaxun/maxun](https://github.com/getmaxun/maxun)
(约 17.6k stars,**AGPL-3.0 License**)。

> 许可边界:AGPL 只作 **plugin 声明依赖 + 文档引用 + compose 引用官方
> 镜像** 三种方式接入;本仓库不复制、不修改、不分发 Maxun 的任何源码。

## 能力

- `provides: [maxun]`:把任意站点录制成机器人,产出结构化数据;myia 侧
  经 L1 `direct_api` 消费其导出接口/REST 端点。
- 装不上不拦核心流水线(铁律)。

## local 模式(docker compose)

Maxun 是多容器栈(postgres + minio + backend + frontend + browser),
先建 `.env` 再起:

```bash
myia plugin install plugins/myia-maxun
cd plugins/myia-maxun
# 从上游 .env.example 复制变量清单,逐项填值(口令自生成,零明文入库):
# DB_USER / DB_PASSWORD / DB_NAME / MINIO_ACCESS_KEY / MINIO_SECRET_KEY /
# BACKEND_URL / PUBLIC_URL / OLLAMA_BASE_URL(可选)……
docker compose up -d
open http://127.0.0.1:5173      # 控制台,注册首个账号即管理员
```

- 与上游 compose 的差异:postgres/minio/browser 不发布宿主端口(走
  compose 内网),backend(8080)/frontend(5173)只绑回环 —— 更安全,
  行为一致。
- 数据在 `postgres_data` / `minio_data` 卷;`docker compose down` 不丢,
  `down -v` 才清。

## remote 模式(零 Docker)

已有部署(自托管实例或上游托管版)时,品类 YAML 填:

```yaml
plugin:
  id: myia-maxun
  modes:
    remote:
      endpoint: https://maxun.example.com   # 换成你的实例地址
```

上游鉴权为控制台会话(浏览器登录),没有稳定的 API token 契约,故本插件
不声明 token;若你在实例前加了带 token 的网关,把 token 写入钥匙链
(`myia secret set myia/maxun/<name>`)后在品类侧按
`keychain:myia/maxun/<name>` 引用。自检只产结构化 warning,不拦核心。

## 凭据红线

- compose 零明文凭据:全部口令经 `${VAR:?}` 从 `.env` 注入;`.env` 不入库。
- 仓库即公开:实例地址只用 `example.com` 占位。

## 安装 / 移除

```bash
myia plugin install plugins/myia-maxun
myia plugin list --json
myia plugin remove myia-maxun
```
