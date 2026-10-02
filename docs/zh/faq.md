# FAQ

> 常见问题:采集伦理与边界、凭据安全、数据生命周期、LLM 成本、故障自修。
> 入门见[快速上手](getting-started.md);字段细节见 [schema 参考](schema.md)。

## 采集伦理与边界

### MYIA 会无视 robots.txt 吗?

不会。`rate_limit.respect_robots` **默认 `true`**,引擎层统一执行:每个源站
的 robots.txt 拉取一次并按源缓存,Disallow 的 URL 直接跳过(结构化
`robots_disallowed`,skip 原因可查)。robots.txt 拉取失败或站点没有
robots.txt 时按「允许」处理(fail open,这是行业惯例的宽容方向),但限速
依然生效。确要关 `respect_robots`(比如目标是公开数据 API 而非页面爬取),
必须在 YAML 注释里写明理由:

```yaml
id: demo-robots
name: robots 演示
schedule: "0 9 * * *"
sources:
  - name: public-data-api
    engine: direct_api
    url: "https://data.example.com/api/v1/items"
    extract:
      type: json_path
      fields:
        title: "$[*].name"
        url: "$[*].url"
    rate_limit:
      qps: 0.5
      respect_robots: false   # 数据 API 而非页面爬取:站点 robots.txt 全站 Disallow 但 API 本身公开
push:
  - channel: stdout
```

### 遇到验证码怎么办?

分层处理:**基础盾**(Cloudflare Turnstile / hCaptcha 质询页、TLS 指纹检测)
是 L4 `scrapling`(隐身指纹 + 可选 Turnstile 求解)与 L5
`stealth_browser`(反检测指纹)的工作范围,降级链会自动尝试。**硬墙**
(手机验证码 / 短信 OTP / 真人人工审核)命中即结构化报错
(`captcha_phone_verification` / `captcha_human_review`),零尝试直接上报——
MYIA 不做绕过。

### 「真人验证 + 手机号」的源能绕过吗?

不能,这是写死的安全底线:**无解也不碰**。MYIA 不提供、也不会提供绕过手段。
L5 `stealth_browser` 会对这类硬墙做识别:零尝试、结构化报错
(`captcha_phone_verification` / `captcha_human_review`)并跳过;其余引擎层没有
验证码识别能力,只会得到泛化失败(如 `http_403`)或空结果——同样绝不尝试绕过。
把这类源从插件里注释掉,或换成有公开 API 的替代数据源。

### MYIA 会伪装成浏览器吗?

默认不会。默认 User-Agent 是诚实的产品标识(`MYIA/0.1 (config-driven
intelligence hub)`)。有站点对产品 UA 返回 429、对浏览器 UA 放行时,才考虑
在源级 `headers` 显式配置浏览器 UA——这是站点兼容性选择,请同时遵守站点
条款与 robots.txt。

### 限速可以关掉吗?

不可以 unlimited。`qps`(默认 0.5,≤ 1000)、jitter、429/5xx 指数退避是
引擎层统一执行的默认行为,不是选项;礼貌抓取是产品前提。你可以把 `qps`
调到合法范围内的更高值,但不能声明「不限速」。

## 凭据与安全

### 为什么不允许明文凭据?

配置文件会被复制、提交、分享——明文凭据是泄露的第一源头。MYIA 在**加载期**
就拒绝凭据类键的明文值(错误码 `credential_plaintext`,退出码 1),只接受
`env:` / `keychain:myia/<scope>/<name>` 引用;凭据值永不回显、永不落日志。
`myia doctor --json` 会核验每个引用是否存在并给出修复动作。

### 我的 Cookie 会泄露吗?

存进系统钥匙链(macOS Keychain / Windows DPAPI)的凭据不进仓库、不进配置
文件、不进日志;`myia secret list` 只列名字。仓库即公开——任何凭据、内网
地址、生产语料零入库是项目自己的红线,官方插件里的凭据位全部是引用占位。

## 数据与存储

### 数据存在哪?会无限膨胀吗?

单 SQLite 文件(默认 `./myia.db`,`--db` 可改)。`storage.retention`(缺省
`90d`)到期自动清理,`storage.vacuum`(缺省 `monthly`)定期 VACUUM 回收
空间;声明 `baseline:` 的品类,数值历史按保留期 2 倍保存(保证周环比窗口)。

### 为什么我的条目被丢弃了?

三个最常见的正常丢弃路径,`myia run --json` 的 `stages[].skips` 都看得到:

1. **`classify_unmatched`**:内置七大类扫描(`classify.builtin: true` 默认开)
   没命中——品类对不上七大类时改 `builtin: false` 并写自定义规则;
2. **去重拦截**:`dedup.key` 渲染出的键见过——同一 URL 或组合键已采集过;
3. **变更指纹未变**:源内容与上轮一致,整个源跳过(`unchanged_skip`)。

### 为什么二跑不重发?

两层机制正交:`push[].route` 管「推不推」(score 分级),AM/PM 槽位
(`{date}`/`{slot}` 组合去重键)管「发没发过」。想让同一源每天早晚各推一次,
用 `"{url}-{date}-{slot}"` 这类组合键,而不是裸 `{url}`(全期只推一次)。

## LLM 与成本

### 必须配 LLM 吗?

不必须。`enrich.enabled: false`(默认)时整条流水线零 token 可跑:关键词
粗筛 + 阈值路由照常工作,趋势基线(`baseline:`)更是零 token 的数值分析。
要开精评才需要 OpenAI 兼容端点(`enrich.base_url`/`api_key`,MYIA 无内置
端点、无默认 key)加 `uv sync --extra llm`。

### 精评预算耗尽会怎样?

自动降级为纯关键词粗筛,run 照常完成并打 WARNING——预算护栏保证的是
「成本封顶」,不是「任务失败」。缓存(`enrich.cache`)让同一 URL 永不打
两次分。

### 事件聚合额外烧 token 吗?

很少。`aggregate:` 的粗筛(标题相似度)零 token,只有候选组进 LLM 确认,
且与 enrich 共享批量/缓存/预算护栏、复用同一端点配置;带时间戳且相差超过
`window_hours` 的候选对连 LLM 都不问。

## 故障与自修

### 源挂了怎么办?

面向 AI 的自修循环:改 YAML → `myia test --json` 验证提取 →
`myia doctor --json` 直到 `findings` 清零 → `--dry-run` 演练 → 正式 run。
源健康度状态机(`ok`/`degraded`/`dead`/`unknown`)与 findings→修复动作
对照表见[插件开发指南](write-a-plugin.md)的自诊断节。

### 退出码怎么读?

`0` 成功;`1` 配置或用法错误(YAML 拒载/明文凭据/用法);
`2` 采集全部失败;`3` 部分失败(部分源或通道失败)。`--json` 输出的
`status` 字段与之对应:`success`/`failed`/`partial`。

## 插件市场

### 插件装不上会怎样?

不怎样——核心流水线照常跑。场景插件(`plugin:` 节声明的依赖)装不上、配置
坏、remote 不可达,都只降级为一条结构化 finding,品类其余部分继续采集推送
(桌面工具的生死线)。装卸命令 `myia plugin list / install / remove`;
社区插件目录见 `plugins/community/README.md`。
