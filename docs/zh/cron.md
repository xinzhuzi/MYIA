# 定时任务(cron)

> 把品类管线挂上时间表:到点自动「跑一遍 + 发消息」。同一底座三种宿主 ——
> CLI 常驻(`myssia cron serve`)、桌面端(内置 ticker)、手动 `myssia cron tick`
> (外接 cron/调试),互斥共存。job 的载荷就是品类 YAML(见
> [写一个插件](write-a-plugin.md)、[schema 参考](schema.md))。

## 1. 三十秒建 job

```bash
myssia cron create "every 2h" --category config/myssia-demo.yaml --deliver local
myssia cron list
myssia cron serve    # 常驻宿主,60s 一轮 tick;Ctrl-C 停
```

`--category` 指向品类 YAML(存绝对路径;创建时走完整装载校验,坏文件当场
exit 1)。一份可直接挂上去的最小品类:

```yaml
id: cron-demo
name: 定时演示
schedule: "0 9 * * 1-5"        # 本字段是品类自身节奏;cron job 用 create 的 schedule 参数
timezone: Asia/Shanghai
sources:
  - name: example-news
    engine: static_html
    url: "https://example.com/news"
    extract:
      type: list
      item: "article"
      fields:
        title: "h2 a"
        url: "h2 a@href"
dedup:
  key: "{url}"
push:
  - channel: stdout
storage:
  retention: 30d
```

每次到期 fire = spawn 一次 `myssia run <品类> --json` 子进程(抓取→分类→去重
→分析→内建推送照常),跑完把**运行结果摘要**(状态/时长/源统计/条目留存/
推送桶/失败行)投递到 `--deliver` 目标;本地留档在 `<数据根>/cron/output/`。

## 2. schedule 语法

### 自然语言

| 形态 | 示例 | 语义 |
|---|---|---|
| 周期 | `30m`、`every 30m`、`every 2h`、`1d` | interval,从上次**完成时刻**重锚 |
| 周几/每天 | `every monday 9am`、`weekdays at 9am`、`every day at 9am`、`monday, wednesday at 14:00` | 转 5 段 cron |
| 一次性延迟 | `in 30m`、`in 2h` | 只发一次(自动 repeat=1) |
| 一次性时刻 | `2026-11-01T09:00:00` | 只发一次;朴素时间戳锚 `--timezone` |
| cron 表达式 | `0 9 * * 1-5` | 见下 |

时间词支持 `9am` / `9:30am` / `14:00` / `7`(裸 24h 小时)/ `noon` /
`midnight`;时长支持 `30m` / `2h` / `1d` 与裸单位(`hour` = 每 1 小时)。

interval 的下一次到期 = 上次完成时刻 + 周期,且要等下一轮 tick(缺省 60s
一轮)才被捞走——`every 1m` 在缺省 tick 下实际节律约 2 分钟一发,要更密就
调小 serve 的 `--interval`。

### 5 段 cron 与 POSIX 周几语义

`分 时 日 月 周`,**限恰好 5 段**(6 段结构化拒收)。周几(dow)按 **POSIX
语义:0 和 7 都是周日**;支持列表(`1,3,5`)、区间(`1-5`)、步进(`*/2`)、
环绕区间(`5-1` = 周五到周一)、英文周名(`MON-FRI`)。写错的报错带五形态
用法清单,照着换形态即可。

### 时区

`--timezone` 优先,缺省取品类 YAML 的 `timezone`,再缺省跟随本地。cron 按
配置时区墙钟匹配,DST 回拨小时严格递增不产生过去时刻。

## 3. deliver:运行摘要投递到哪

- `local`(缺省):摘要落 `<数据根>/cron/output/<job_id>/`;
- 平台 spec:`feishu:群名`、`telegram:12345`(复用消息平台层的目录/直达
  解析,凭据走通道默认 env/keychain 引用链);
- `stdout:debug`:调试用,摘要卡片以 JSON 行回显到日志;
- `--failure-deliver`:运行失败时的告警目标,同 spec 语法;缺省回落
  `--deliver`,`none` 显式关闭。运行成功但投递失败记
  `last_status="delivery_failed"`,不累加失败连击。

## 4. 宿主形态:谁在跑 tick

### CLI 常驻(serve)

```bash
myssia cron serve --db /data/myssia.db --interval 60
```

headless/服务器形态:监督守护线程跑 ticker(线程崩了自动 respawn),
`myssia cron status` 查心跳活性与下次到期时刻。

### 桌面端

桌面应用启动即起同款 ticker(仅 home 模式),`cron.*` 协议方法族供 UI 消费;
与 CLI serve 并存安全(见下)。

### 手动 tick(外接 cron)

```bash
myssia cron tick --db /data/myssia.db
```

单次扫描派发所有到期 job——给系统 crontab/CI 外接调度用;抢不到锁(其他
宿主在跑)时安静返回 0。

### 多宿主共存

tick 文件锁全进程单飞 + fire 认领(TTL)双保险:serve、桌面、手动 tick 同
时开,同一 job 同一时刻只跑一份。桌面端有用户手点运行时,cron 该次 fire
跳过(`skipped_busy`),用户优先。

## 5. 行为注记

### digest 留池不跨 fire 攒

digest 槽位聚合是单次 run 内的内存态:每次 fire 独立起一条管线,digest 槽
条目在该 run 末尾合成一张卡随 run 发出,**不会跨 fire 累积**(想攒日报就把
schedule 排疏,而不是依赖跨 fire 留池)。

### 与 `run --loop` 并存

cron 的 fire 是单次 run 子进程,不启动消息轮询,与 `run --loop` 常驻形态
零冲突;既有约束照旧 —— 同一 telegram bot token 至多一个 `--loop` 进程
(409 冲突),cron 不改变这一点。同一 db 的多个 job 派发串行,不同 db 并行。

### docker compose 常宿

官方 compose 默认 `command: run … --loop`;要定时形态就把
`docker/docker-compose.yml` 里的 command 改为 `cron serve --db /data/myssia.db`
(镜像入口就是 `myssia` CLI),job 存进 `/data/cron/jobs.json` 随卷持久化。job
在容器里建——品类 YAML 只读挂在 `/config/`(演示件开箱即用;自建品类先在
宿主机侧复制 `*.example.yaml` 模板填好):

```bash
docker compose -f docker/docker-compose.yml run --rm myssia \
  cron create "every 2h" --category /config/myssia-demo.yaml --db /data/myssia.db
```
