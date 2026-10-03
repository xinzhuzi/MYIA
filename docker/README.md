# Docker 部署(世事 服务端形态)

三形态之一:想在服务器/NAS 上 24h 跑的用户 `docker compose up`,同一份插件 YAML、同一个核心(容器内就是 APScheduler 定时,不引 n8n/Kestra 等编排平台)。

> v1.1 插件层架构转向:桌面路径(默认)**零 docker** —— 插件以源码/
> 进程内(submodule + 适配器)或 remote 已部署服务接入,见
> `plugins/shishi-*/README.md`。docker 目录只承载两类可选部署:世事 核心的
> 服务端形态(本文件)与场景件的服务端形态(`plugins/` 下的子目录)。

## 快速开始

```bash
# 1. 准备凭据文件(docker/.env 已被 .gitignore 忽略,严禁提交)
cp docker/env.example docker/.env
vi docker/.env                      # 填入 FEISHU_* 等;按需增删条目

# 2. 准备数据目录(容器内以 uid 1000 运行)
mkdir -p data && chown -R 1000:1000 data

# 3. 构建并常驻启动
docker compose -f docker/docker-compose.yml up -d --build

# 4. 看日志 / 停止
docker compose -f docker/docker-compose.yml logs -f shishi
docker compose -f docker/docker-compose.yml down
```

首次运行验证:`docker compose ... run --rm shishi --version` 应输出 `shishi <版本>`。

## 运行哪个插件?

compose 的启动命令是 `shishi run /config/plugins/${MYIA_PLUGIN:-stocks.yaml} --loop --db /data/shishi.db`。要跑其他插件,在 `docker/.env` 加一行:

```
MYIA_PLUGIN=ai-news.yaml
```

或用 `docker compose run --rm shishi run /config/plugins/wool.yaml --db /data/shishi.db` 手动单跑一次。

- `--loop`:常驻模式,按插件 YAML 的 `schedule`(cron)+ `timezone` 到点自动触发,由容器 `restart: unless-stopped` 保活。
- `--db /data/shishi.db`:SQLite 落在挂载卷里,条目/去重注册表/engine_hints/run 历史重启不丢。

## 目录与卷

| 宿主机(仓库内) | 容器 | 说明 |
|---|---|---|
| `plugins/` | `/config/plugins`(只读) | 与本地 CLI **同一份** YAML,零修改复用 |
| `data/` | `/data` | SQLite 等持久状态;需 chown 1000:1000(或改 `MYIA_UID/MYIA_GID`) |

`docker/.env` 不挂载、不进镜像;它只在 `up` 时被 compose 读成环境变量。

## 场景件的服务端形态(plugins/ 子目录)

官方场景件的本地部署文件统一收在 `docker/plugins/<id>/compose.yml`(插件
目录 `plugins/shishi-*/` 内零 docker 内容;桌面用户不需要这些):

| 插件 | 分级(tier) | compose | 说明 |
|---|---|---|---|
| shishi-proxy | desktop | `docker/plugins/shishi-proxy/compose.yml` | 完整 proxy_pool 服务形态(定时抓取+Redis 池+API);桌面已有进程内轻量路径 `shishi proxy` |
| shishi-osint | desktop | `docker/plugins/shishi-osint/compose.yml` | Photon 的容器化跑法(CLI 工具,非服务) |
| shishi-monitor | remote | `docker/plugins/shishi-monitor/compose.yml` | changedetection.io 实例;桌面内置变更指纹已覆盖主场景,实例为可选 |
| shishi-douyin | server-only | `docker/plugins/shishi-douyin/compose.yml` | 抖音/TikTok 数据 API 单容器快速通道 |
| shishi-maxun | server-only | `docker/plugins/shishi-maxun/compose.yml` | Maxun 多容器栈;先在该目录建 `.env`(口令自生成,零明文入库) |

```bash
docker compose -f docker/plugins/shishi-monitor/compose.yml up -d
```

## 凭据与安全底线

- **compose 文件与镜像零明文凭据**。所有秘密走 `docker/.env` → 容器环境变量;插件 YAML 只写 `env:VAR` 引用,`src/shishi/schema.py` 运行期解析。
- 变量缺失不会静默:对应 `env:VAR` 解析失败即结构化报错(`env_var_missing`)并以退出码 1 拒跑。
- `keychain:` 引用在容器内不可用(v0.1 仅支持 `env:` 引用)。
- Web 面板尚未实现(后续版本,定位=旁观窗口);compose 中的 `web` 服务只是注释占位,启用前请勿放开。

## 时区(TZ)

- 插件 YAML 声明了 `schedule.timezone`(如 `Asia/Shanghai`)的,cron 按该时区触发,与 TZ 无关。
- TZ 环境变量(默认 `Asia/Shanghai`)决定:插件**未声明** timezone 时的系统时区回退,以及 AM/PM 推送槽位的本地 12:00 分界(`src/shishi/dedup.py`)。
- 镜像内置 `tzdata`;在 `docker/.env` 加 `TZ=UTC` 即可改时区。

## 预构建镜像(GHCR)

CI(main 分支推送、`v*` tag)自动构建多架构镜像发布到 `ghcr.io/xinzhuzi/shishi`。不想本地构建时,删掉 compose 里的 `build:` 块,然后:

```bash
docker compose -f docker/docker-compose.yml pull
docker compose -f docker/docker-compose.yml up -d
```

## 排障

- 日志:`docker compose logs shishi`;`--json` 机器可读输出可用 `docker compose run --rm shishi run /config/plugins/stocks.yaml --db /data/shishi.db --json`。
- 退出码契约:0 成功 / 1 配置错误(含凭据缺失)/ 2 采集全部失败 / 3 部分失败 —— `docker compose run` 场景下可用 `$?` 检查。
- 单源失败不拖垮整个品类;skip(变更指纹未变/去重拦截)是正常路径,日志可见原因。
