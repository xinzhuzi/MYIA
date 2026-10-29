# 代理池(proxy pool)

> 给源挂代理不再绑死单出口:全局 `pools.yaml` 声明**池**,品类 YAML 只写
> `proxy: pool:<名称>` 引用。多上游按声明顺序轮换,transport 级连败摘除
> (被动健康),半开到期由流量自然试炼恢复,全池摘除即熔断零网络快速失败
> (每池熔断)。CLI 侧 `--config` 注入,桌面侧「设置 · 代理池」卡入凭据 +
> G10 一键连通性测试,「代理池」面板看出口列表/测活/探测成功率。字段
> 速查见 [schema 参考](schema.md) 的 pools 节;凭据引用形态见
> [快速上手](getting-started.md) 第 2 节。

## 1. 三十秒接一个池

先凭据入钥匙链(池 URL 的 userinfo 只允许 `env:` / `keychain:` 引用,
明文在加载期即拒载;值走 stdin 管道,不落 shell history):

```bash
myssia secret set myia/proxy/b < key.txt
```

再写全局配置(桌面端缺省自动发现 `<数据根>/pools.yaml`,CLI 侧由
`--config` 显式传入;仓库无随附样例件,按下方 YAML 自建即可):

```yaml
pools:
  main: "http://env:MYIA_PROXY_MAIN@proxy.example.com:8080"   # 单上游(v0.2 字符串形态)
  rotating:                                                    # 多上游池(v1.2 池化形态)
    upstreams:
      - "http://env:MYIA_PROXY_A@p1.example.com:8080"
      - "socks5://keychain:myia/proxy/b@p2.example.com:1080"
    max_failures: 3          # 可选;连续 transport 失败摘除阈值,默认 3;须 ≥1
    probe_interval: 300      # 可选;半开恢复间隔(秒),默认 300;须 ≥1
```

品类 YAML 的源只写引用,永不出现代理凭据:

```yaml
id: proxy-pool-demo
name: 代理池演示
schedule: "0 9 * * *"
sources:
  - name: example-news
    engine: static_html
    url: "https://example.com/news"
    proxy: pool:rotating        # 引用全局配置声明的池;direct = 不走代理
    extract:
      type: list
      item: "article"
      fields:
        title: "h2 a"
        url: "h2 a@href"
push:
  - channel: stdout
```

```bash
myssia run --config pools.yaml config/proxy-pool-demo.yaml
```

`--config` 是 run / test / doctor 共用的同一加载器:坏文件当场 exit 1,
每条错误带字段路径 + 错误类(`invalid_pool_upstreams` / `invalid_pool_policy`
/ `unknown_pool_field` 等,拼写错误在这里拒载、不会被静默吞掉)。池名只允许
字母/数字/连字符/下划线(与 `pool:<名称>` 语法同口径);上游 scheme 限
`http` / `https` / `socks5` / `socks5h`(socks 依赖 `httpx[socks]` extra,
缺依赖是结构化报错而非网络失败)。字符串形态与映射形态同一引擎:字符串
等价于 `upstreams=(url,)` 的单上游池。

## 2. 运行语义:轮换 / 被动健康 / 半开恢复 / 每池熔断

健康模型是**被动的、零后台任务**:没有探活定时器,一切状态由真实流量
派生;生命周期 = 一个 run(run 结束健康状态即焚,下次 run 从全健康起步)。

- **轮换**:每次请求从顺序游标的下一站起,按声明序环形扫第一个可 admit
  的健康上游;transport 失败的上游不被下一 attempt 连续复骑(池大小 > 1
  时),源 retry 预算花在其余上游上。
- **被动健康摘除**:每上游维护**连续** transport 级失败计数(`httpx.
  TransportError` 含超时族;HTTP 4xx/5xx 不计数也不清零——代理已送达,
  锅是源的),达 `max_failures`(默认 3)摘除;一次送达(状态 < 400)
  计数清零、摘除复位归队。
- **半开恢复**:摘除上游距摘除时刻过 `probe_interval`(默认 300s)后,
  由下一次流量自然触发**单飞试炼**——成功归队,失败立即重摘除(不再另攒
  N 次);零后台探活。
- **每池熔断**:全部上游摘除且无到期半开 = 池熔断,后续请求**零网络**
  快速失败(`proxy_pool_exhausted`,错误携带最早恢复秒数;降级链同
  `proxy_*` 家族短路),不睡等半开,把决定权交还源级降级。

浏览器引擎(stealth_browser / crawl4ai / llm_browser 等)在 mount 时刻
消费同一池 facade 的当前可 admit 上游;图片环的流式下载也骑池通道
(`stream` 形态,headers 到达即算送达)。同池多源共享同一条池通道
(一个 run 内每池一个 facade),run 收尾统一关闭全部上游 client。

## 3. doctor:逐上游连通性探测

```bash
myssia doctor --config pools.yaml --json --probe-timeout 10
```

逐池保序、池内全部上游**并入同一并行集**(asyncio.gather):每个上游各吃
自己的 `--probe-timeout`(默认 10s),总耗时约等于最慢上游而非累加——探测
必须在桌面 sidecar 的固定壳超时(120s)内回话。探测是一次轻量 GET,应答端
点回出口 IP(`exit_ip` 字段,可直接核对是否走了预期出口)。每行结果:

| 键 | 语义 |
|---|---|
| `pool` / `upstream_index` / `upstreams` | 池名 / 池内序号 / 池内上游总数(单上游池恒 `0` / `1`,对既有消费方是纯增字段) |
| `proxy_url` | 上游 URL(凭据已打码,展示形态与日志一致) |
| `ok` / `latency_seconds` / `status_code` | 探测是否通过(< 400)/ 延迟 / HTTP 状态码 |
| `exit_ip` | 经该上游出网的出口 IP |
| `message` / `error_type` | 人话原因 / 错误类(`proxy_error` / `proxy_timeout` / `proxy_network` / `http_<状态>` 等) |

单池凭据解析失败不拖垮其余池(部分失败语义),失败池回一行兜底行并留
打码 URL 与错误类。

## 4. 桌面端

### 设置屏:凭据入钥匙链 + 一键连通性测试(G10)

设置 → 通用 →「代理池」卡两件事:

- **池凭据入钥匙链**:填池名 + 凭据值,保存即写
  `myia/proxy/<池名>`(值永不回显;同池名再存新值就是换密码)。全局
  pools YAML 侧以 `keychain:myia/proxy/<池名>` 引用;池 URL 结构的写回
  属协议缺口,`pools.yaml` 仍手写。
- **一键连通性测试**:「全局 pools YAML 路径」填路径走显式 `--config`
  探测,**留空则自动发现 `<数据根>/pools.yaml`**(命中才探测,未命中如实
  提示,不冒错)。结果回显在下方「保存后验证(doctor 回显)」卡——一切
  回显来自 doctor 应答,与 CLI 同一条代码路径。

### 代理池面板:出口列表 / 测活 / 探测成功率

侧栏「配置」组 →「代理池」(路由 `/proxy-pool`),协议面两个方法
(sidecar `proxy_panel.py`):

- `proxy.probe` `{config?, config_auto?, probe_timeout?}`:探测 = 字面
  内部复用 doctor(零逻辑分叉),应答为 doctor 应答原形状 + 纯增键
  `snapshot: {persisted, reason?}`;探测成功后把打码池行作为一条快照
  追加落盘(落盘失败不 fail 探测,`persisted=false` + 原因如实回)。
- `proxy.history` `{limit?, pool?}`:零网络纯读,`{snapshots, count,
  truncated, home_available, skipped}`(新→旧);limit 钳制 [1,100] 缺省
  20,`pool` 给值只回该池行;坏行跳过且 `skipped` 如实计数。

面板行为(V1 只读):**进屏零网络**(挂载只读历史,绝不挂载即探测),
**探测只由用户点「探测」按钮触发**(零后台探活)。四块内容:

1. **总览卡**:最近一次探测的分组计数(池数 / 已解析上游 / 可用 / 不通 /
   未探测);
2. **每池健康卡**:每声明上游一行——打码 URL + 三态徽章(连通·延迟 /
   不通 / 未探测)+ 出口 IP + 人话原因 + 错误类短标签;
3. **探测历史区**:每池**探测成功率**(近 20 条窗口内 ok 行占比;探测口径
   非流量成功率)与快照时间线;
4. **空态**:无缺省 `pools.yaml` 时提示去设置屏入凭据;开发模式无数据根
   时探测可用但历史不落盘。

快照落盘 = `<数据根>/proxy-probe-history.jsonl`,一行一条
`{at, config, pools}`(池行已打码);保留帽 200 条,超帽裁最旧并原子重写;
文件创建即 0600(内容虽已打码,防御纵深)。**仅面板发起的探测落历史**
(设置屏的探测走 doctor 回显,不落历史)。面板不写回 `pools.yaml`
(上游增删改属 V2)。

## 5. 行为注记与红线

- **行为变化披露(v1.2)**:字符串(单上游)池的失败语义从「逐源各自
  重试」变为「3 连败摘除 → 池熔断快速失败」——YAML 兼容指可原样加载、
  请求照跑,失败路径不再逐字节等价(死上游不再被每源反复烧穿重试预算)。
- **凭据红线**:品类 YAML 与 `pools.yaml` 只写 `env:` / `keychain:`
  引用,明文加载期拒载;一切展示(日志 / doctor / 面板 / 历史)一律打码
  形态,凭据值永不入结果。
- **`residential:<区域>` 仍是结构化未实现**(`proxy_not_scheduled`),
  住宅代理轮换留给后续插件;当前可用形态就是 `direct` 与 `pool:<名称>`。
- **健康状态不跨 run**:摘除/熔断都是 run 内的内存态,重启 run 即全健康
  重新起步;跨 run 要看的「趋势」用面板的探测历史,不用运行态。
