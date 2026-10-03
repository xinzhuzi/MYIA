# 零成本接入

> 不花一分钱跑通看图与精评:本地端点(默认,零出网)→ 云端免费端点 → 轻量日批。
> **所有免费额度均为快照 2026-10-03 的时点信息,以各官网为准**;会耗尽/会到期的
> 营销型额度均已显式标注性质。

MYIA 不内置任何 LLM 端点、没有默认 key([快速上手](getting-started.md)第 2 节)。
看图(vision)与精评(enrich)都只认 **OpenAI 兼容端点**——换 `base_url` 即可接入
下述任何一家。两条路径的配置面不同,接入步骤分开给:

| 路径 | 配置位置 | 凭据写法 |
|---|---|---|
| 看图 vision | `<数据根>/vision.yaml`(桌面端「看图设置」同源,手改亦生效) | 云端 key 只收 `keychain:` 引用 |
| 精评 enrich | 品类 YAML `enrich:` 节 | `env:` 或 `keychain:` 引用 |

## 1. 本地端点(默认):mlx-vlm / Ollama

模型跑在本机:额度无限、零出网、零额度焦虑,看图与精评都完全适用,也是产品
默认形态(macOS 上 mlx-vlm 为主,Linux/跨平台用 Ollama 或 LM Studio)。

**看图**(vision 的 local 通道即缺省):启动一个本地 OpenAI 兼容视觉服务,

```bash
uvx --from mlx-vlm mlx_vlm.server --model <模型目录> --host 127.0.0.1 --port 8080
```

- 缺省端点 `http://127.0.0.1:8080/v1`(vision.yaml 的 `local.base_url`),
  `local.model` 填本地模型目录路径;本地通道零鉴权头,不需要任何 key。
- 用 LM Studio 就把 `local.base_url` 改成 `http://127.0.0.1:1234/v1`;
  用 Ollama 改成 `http://127.0.0.1:11434/v1`(Ollama 官方 OpenAI 兼容端点)。

**精评**(enrich):端点指向本地即可。注意 MYIA 无默认 key,本地免鉴权端点
也要给一个非空占位值:

```bash
export MYIA_LLM_BASE_URL=http://127.0.0.1:11434/v1
export MYIA_LLM_KEY=ollama            # 占位值,本地端点不校验
```

代价是显存与速度:视觉模型建议 Apple Silicon 上 8B 级 MLX 量化版,纯文本
评分 2B-8B 即够。跑不动的机器走下面云端免费档。

## 2. 云端免费端点

按「限速制免费」(不设总额度、只限速率)优先于「营销型额度」(用尽即止)排列:

| 服务 | 免费内容(快照 2026-10-03,以官网为准) | 性质 | 看图 | 精评 |
|---|---|---|---|---|
| 智谱 GLM-4V-Flash / GLM-4-Flash | 定价页输入/输出单价均标注「免费」(0 元,非限时活动);并发/RPM 数值不公开,登录后于账户速率限制页查看 | 限速制 | ✓(4V-Flash) | ✓(4-Flash 即精评缺省模型 `glm-4-flash`) |
| OpenRouter `:free` 模型 | 当日 466 个模型中 17 个以 `:free` 结尾;未充值 50 请求/天,累计充值 ≥$10 后 1000 请求/天(官方 FAQ) | 日配额,用尽即止 | 未核到稳定免费视觉模型,不推荐 | ✓ 文本主力 |
| Groq Free plan | 文本模型(如 `openai/gpt-oss-120b`)30 RPM、1000 请求/天、8K TPM(官方 rate-limits 页);限速按组织计 | 限速制 | 未核 | ✓ 备选 |
| Mistral La Plateforme | 免费层为新账户默认(无需信用卡),官网定价页标注含 $10/月 API credits;具体限速登录控制台 Limits 页自查 | 赠金制,月度会用尽 | 未核 | ✓ 备选 |

> 未列入:SiliconFlow / DashScope。SiliconFlow 注册赠送额度截至快照日在官方公开
> 渠道(官网/注册页/财务 FAQ)已无明文,金额与有效期无法核实,本文不对用户承诺;
> 若自行使用,一律按「赠额用尽即止」对待。

### 接入:看图(cloud 通道)

智谱是看图云端通道的缺省厂商,零成本只需换模型名 + 录 key:

```text
# <数据根>/vision.yaml(桌面端「看图设置」同源;手改亦生效)
channel_default: cloud          # 缺省 local,切到云端
cloud:
  base_url: https://open.bigmodel.cn/api/paas/v4   # 产品缺省即此
  model: glm-4v-flash           # 0 元视觉模型(确切标识以智谱模型页为准)
  api_key: keychain:myia/image/api_key
```

两点注意:

- 产品缺省云端模型 `glm-4.6v` 是**付费档**,零成本必须把 `model` 改成免费模型名;
- `cloud.api_key` 只收 `keychain:` 引用(明文与 `env:` 一律拒载),先录钥匙链:

```bash
myia secret set myia/image/api_key < key.txt    # 值走 stdin,不落 shell history
```

### 接入:精评(enrich)

品类 YAML 的 `enrich:` 节(字段:`enabled` / `model` / `base_url` / `api_key`
等),端点与 key 都写凭据引用;缺省模型 `glm-4-flash` 本身就是智谱免费档。
完整品类示例(其余节取最小骨架,`enrich:` 节即主角):

```yaml
id: zero-cost-demo                    # 存成 plugins/zero-cost-demo.yaml 即可跑
name: 零成本精评演示
schedule: "0 9 * * *"
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
enrich:
  enabled: true
  model: glm-4-flash                              # 缺省即此;确切标识以智谱模型页为准
  base_url: env:MYIA_LLM_BASE_URL
  api_key: env:MYIA_LLM_KEY
push:
  - channel: stdout
```

```bash
export MYIA_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
export MYIA_LLM_KEY=<你的智谱 API key>            # 或改用 keychain: 引用入钥匙链
```

换其他厂商只改 `base_url` + `model`:OpenRouter `https://openrouter.ai/api/v1`
(模型名带 `:free` 后缀)、Groq `https://api.groq.com/openai/v1`、Mistral
`https://api.mistral.ai/v1`(三家的 OpenAI 兼容地址均出自官方文档)。免费档
配额紧张时调小 `enrich.batch`,预算护栏 `budget_per_run` 照常生效。

## 3. 轻量日批:Gemini 免费层

Google AI Studio 免费层适合**每天几批的轻量精评**。官方 rate-limits 页截至快照日
已不再公布免费档具体数字,登录
[aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit) 自查;
定性结论:轻量日批够用,重批/高频不够。OpenAI 兼容端点(官方文档):

```bash
export MYIA_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
export MYIA_LLM_KEY=<你的 AI Studio API key>
```

精评 `model` 填 Gemini 模型名;看图的免费视觉配额未核实,本文不承诺。

## 红线与习惯

- **额度会变**:上表所有数字为快照 2026-10-03,以各官网为准。免费层是营销手段,
  可能新增收费墙、收紧限速或下线;接入前扫一眼官网定价页。
- **凭据不落明文**:YAML 只写 `env:` / `keychain:` 引用,值走 `myia secret set`
  的 stdin 管道;看图云端 key 只收 `keychain:`。
- **营销型额度不押注**:凡「赠金 / credits / 一次性额度」都可能归零,关键品类
  不要把免费档当唯一依赖,本地端点做兜底。
