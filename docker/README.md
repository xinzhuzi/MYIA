# Docker 部署(MYIA 服务端形态)

三形态之一:想在服务器/NAS 上 24h 跑的用户 `docker compose up`,同一份插件 YAML、同一个核心(容器内就是 APScheduler 定时,不引 n8n/Kestra 等编排平台)。

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
docker compose -f docker/docker-compose.yml logs -f myia
docker compose -f docker/docker-compose.yml down
```

首次运行验证:`docker compose ... run --rm myia --version` 应输出 `myia <版本>`。

## 运行哪个插件?

compose 的启动命令是 `myia run /config/plugins/${MYIA_PLUGIN:-stocks.yaml} --loop --db /data/myia.db`。要跑其他插件,在 `docker/.env` 加一行:

```
MYIA_PLUGIN=ai-news.yaml
```

或用 `docker compose run --rm myia run /config/plugins/wool.yaml --db /data/myia.db` 手动单跑一次。

- `--loop`:常驻模式,按插件 YAML 的 `schedule`(cron)+ `timezone` 到点自动触发,由容器 `restart: unless-stopped` 保活。
- `--db /data/myia.db`:SQLite 落在挂载卷里,条目/去重注册表/engine_hints/run 历史重启不丢。

## 目录与卷

| 宿主机(仓库内) | 容器 | 说明 |
|---|---|---|
| `plugins/` | `/config/plugins`(只读) | 与本地 CLI **同一份** YAML,零修改复用 |
| `data/` | `/data` | SQLite 等持久状态;需 chown 1000:1000(或改 `MYIA_UID/MYIA_GID`) |

`docker/.env` 不挂载、不进镜像;它只在 `up` 时被 compose 读成环境变量。

## 凭据与安全底线

- **compose 文件与镜像零明文凭据**。所有秘密走 `docker/.env` → 容器环境变量;插件 YAML 只写 `env:VAR` 引用,`src/myia/schema.py` 运行期解析。
- 变量缺失不会静默:对应 `env:VAR` 解析失败即结构化报错(`env_var_missing`)并以退出码 1 拒跑。
- `keychain:` 引用在容器内不可用(v0.1 仅支持 `env:` 引用)。
- Web 面板尚未实现(后续版本,定位=旁观窗口);compose 中的 `web` 服务只是注释占位,启用前请勿放开。

## 时区(TZ)

- 插件 YAML 声明了 `schedule.timezone`(如 `Asia/Shanghai`)的,cron 按该时区触发,与 TZ 无关。
- TZ 环境变量(默认 `Asia/Shanghai`)决定:插件**未声明** timezone 时的系统时区回退,以及 AM/PM 推送槽位的本地 12:00 分界(`src/myia/dedup.py`)。
- 镜像内置 `tzdata`;在 `docker/.env` 加 `TZ=UTC` 即可改时区。

## 预构建镜像(GHCR)

CI(main 分支推送、`v*` tag)自动构建多架构镜像发布到 `ghcr.io/xinzhuzi/myia`。不想本地构建时,删掉 compose 里的 `build:` 块,然后:

```bash
docker compose -f docker/docker-compose.yml pull
docker compose -f docker/docker-compose.yml up -d
```

## 排障

- 日志:`docker compose logs myia`;`--json` 机器可读输出可用 `docker compose run --rm myia run /config/plugins/stocks.yaml --db /data/myia.db --json`。
- 退出码契约:0 成功 / 1 配置错误(含凭据缺失)/ 2 采集全部失败 / 3 部分失败 —— `docker compose run` 场景下可用 `$?` 检查。
- 单源失败不拖垮整个品类;skip(变更指纹未变/去重拦截)是正常路径,日志可见原因。
