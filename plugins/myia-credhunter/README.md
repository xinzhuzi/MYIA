# myia-credhunter — 凭证猎手(credhunt / credcheck / exposure)

官方场景件,v0.1 骨架。把「发现 → 验证/余额 → 曝面」三段 AI 凭证情报
能力**原生**融进 MYIA 管线的进程内插件(desktop 分级,零 Docker 零服务
依赖)。已部署 aipocket 实例的远程聚合接入走姊妹件 `myia-credentials`
(remote 分级),两件共存、互不替代。

## 授权定位(先读,硬边界)

本插件**仅用于已授权安全研究与自有/已授权资产的泄露凭证排查**:

- 对你自己持有或获得书面授权的资产、代码仓库、暴露面做凭证泄露排查;
- 不做大规模扫描、不打高频探测、不滥用任何发现的凭据(验证/余额探测
  串行 + 每供应商限速,见缺省参数表);
- 凭据命中物按**私有情报红线**处理:不入 git、不外发;items 与一切推送
  模板一律**前 8 后 4 掩码**(grill Q9),全文密钥永不进模板上下文;
- 被动探测只做 L0 未授权读(unauth_read);weak_password/idor/ssrf/
  sqli/rce 级探测 fail-closed 不开,授权范围机制留二期。

## 许可边界

上游参考对象为 AGPL-3.0 项目。本插件是其行为的**功能重实现**:实现只
认本仓行为规格文档(`.trellis/tasks/10-03-aipocket-fusion/research/
behavior-specs/`),零上游代码复制、不搬上游标识符/文案/注释;本包全部
代码与数据由 MYIA 侧从零创作。

## 能力面(provides)

| 能力名 | 段 | 状态 |
|---|---|---|
| `credhunt` | GitHub 工件凭证猎取(code search + commit message 两泳道,联合正则十大族) | v0.1 骨架:指纹库 + 本地扫描已落地,出网猎取排期件 |
| `credcheck` | 凭证验证(models 三态)+ 余额/身份探测 | 排期件(读库后处理,CLI 子命令形态) |
| `exposure` | FOFA/Shodan 曝面发现 + L0 被动探测 | 排期件;无 key 时显式空态不报错 |

v0.1 已落地:供应商指纹库(发现层 20 查询包 + 验证层 25 规格,数据
文件化,`credhunter/data/*.yaml` —— **加供应商 = 加数据不改码**)、
密钥指纹(联合正则十大族 + 17 条细正则 + 变量名归因表 + 噪声过滤)、
items 形状与 Q9 掩码、适配器双入口(引擎面 `fetch` 异步 / CLI 面 `run`
同步)。

## 接入

### 品类 YAML(引擎通路,排期接线)

品类 source 写 `engine: credhunter`(EngineName 注册属接线段任务),
适配器 `fetch(documents)` 产出管线 items,下游 classify/dedup/store/push
零改动复用。dedup 键建议直接引用指纹库口径:

```yaml
dedup:
  key: "{provider}-{key_fingerprint}"   # 结构化字段组合,{title} 永不进键
```

### CLI(宿主动态加载)

宿主按 `plugins/myia-credhunter/adapter.py` compile+exec 挂载(与
`myia proxy` 同一加载器),密钥一律**经参数注入**:

```python
adapter.run(documents=[{"text": "...", "url": "https://github.com/o/r/blob/...", "source_type": "code_snapshot"}])
```

## keychain 引用写法

适配器自身**不读钥匙链、不读环境变量、不落盘**;`keychain:` 引用由
宿主(CLI/引擎)解析成值后经参数传入。规范名空间 `myia/credhunter/*`:

```bash
myia secret set myia/credhunter/github-token   # GitHub 猎取凭据
myia secret set myia/credhunter/fofa-key       # FOFA 曝面发现
myia secret set myia/credhunter/shodan-key     # Shodan 曝面发现
```

品类/配置侧引用写法(与 myia-credentials 同一口径,只许 keychain:):

```yaml
token: keychain:myia/credhunter/github-token
```

无 key 时对应 lane 报显式空态(`credential_missing`,status=empty),
不报错不静默。

## 缺省参数表(grill Q8 定案 + 行为规格默认值)

| 参数 | 缺省 | 说明 |
|---|---|---|
| GitHub code 搜索分页 | ≤5 页 × 100 条/页 | 返回数 < per_page 提前停页 |
| GitHub 查询预算 | 12 查询/run | checkpoint 游标跨 run 轮转 |
| GitHub 限速等待上限 | 90s | 仅 403/429 触发降级,只重试一轮 |
| GitHub blob 尺寸上限 | 1 MiB | 超限弃用,无截断降级 |
| credcheck 并发 | 串行,每供应商 RPM ≤30(间隔 2s) | 不抄上游并发 20 |
| 探测超时 | 15s | 验证/余额单次请求 |
| FOFA | 页大小 100、≤10 页、页间 0.3s、24 查询/run | base 可配(官方/代理部署者自决) |
| Shodan | 页大小 100、≤10 页、页间 1.0s、16 查询/run | — |
| 被动探测 | 仅 L0 unauth_read、每目标 12 请求预算 | 高危类 fail-closed |
| 掩码 | 前 8 后 4(Q9) | items/推送模板一律掩码 |

## 目录

```
plugin.yaml                  # manifest(tier: desktop,adapter in_process)
adapter.py                   # 动态加载入口(引擎面 fetch / CLI 面 run)
credhunter/
  packs.py                   # 发现层 20 包 loader(data/provider_packs.yaml)
  specs.py                   # 验证层 25 规格 loader + resolve(data/provider_specs.yaml)
  fingerprints.py            # 联合正则十大族 + 17 细正则 + 归因表 + 噪声过滤
  findings.py                # items 形状 + dedup 键 + Q9 掩码
  data/provider_packs.yaml   # 发现层查询包(加供应商=加数据)
  data/provider_specs.yaml   # 验证层规格(加供应商=加数据)
```

## 安装 / 移除

```bash
myia plugin install plugins/myia-credhunter
myia plugin list --json
myia plugin remove myia-credhunter
```
