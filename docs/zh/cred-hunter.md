# 凭证猎手(cred-hunter)

> 把「发现 → 验证/余额 → 曝面」三段 AI 凭证情报能力原生融进管线:品类源写
> `engine: credhunter`,进程内场景件 `myssia-credhunter` 就地装配 items(不抓
> `url`),产出**掩码-only**(前 8 后 4)——全文密钥只落本机密钥库。**仅用于
> 已授权安全研究与自有/已授权资产的泄露凭证排查**(授权边界见文末红线)。
> 桌面侧「凭证猎手」面板只读展示密钥库:key / 供应商 / 验证状态 / 高危视图,
> 不导出、不回显全文。引擎词汇表见 [schema 参考](schema.md),插件开发面见
> [写一个插件](write-a-plugin.md)。

## 1. 三条 lane 与品类接入

`engine: credhunter` 是**链外源引擎**:注册于引擎词表但**不参与 auto 的
L1-L6 降级链**,显式选择才生效;失败 = 源级结构化失败,不拦品类里其余源。
`extract` 节不被消费(配了会被结构化拒绝,防静默错配)。三条 lane 经
`engine_options.credhunter.lane` 选择:

| lane | 干什么 | 出网 |
|---|---|---|
| `scan`(缺省) | 本地文本分诊:`documents` 贴可疑日志/配置/diff 片段,供应商指纹库(联合正则十大密钥族 + 17 条细正则 + 变量名归因 + 噪声过滤)扫描产出掩码 items | 零出网 |
| `credhunt` | GitHub 工件猎取:code search + commit message 两泳道;token 池逐请求轮转 + 403/429 退避;跨 run checkpoint 续传;命中入本地密钥库 | GitHub API |
| `exposure` | FOFA/Shodan 曝面发现 + L0 被动探测(仅未授权读) | FOFA/Shodan/目标 |

可直接落地的品类(`exposure` + `scan` 双源,凭据全 `keychain:` 引用):

```yaml
id: cred-hunter-demo
name: 凭证猎手演示
schedule: "0 3 * * *"
timezone: Asia/Shanghai
sources:
  - name: exposure-scan
    engine: credhunter              # 链外源引擎(myssia-credhunter 场景件)
    url: "https://fofa.info"        # 上游锚点(真实调用走 engine_options)
    engine_options:
      credhunter:
        lane: exposure
        fofa_apikey: keychain:myia/credhunter/fofa-key
        fofa_base: https://fofa.info
        shodan_apikey: keychain:myia/credhunter/shodan-key
    proxy: direct
    retry: 0                        # 泳道自带页间限速,不吃引擎重试

  - name: manual-triage
    engine: credhunter
    url: "https://api.github.com"   # 占位锚点:scan lane 不发任何请求
    engine_options:
      credhunter:
        lane: scan
        documents:                   # 待分诊片段(text 为人工合成占位键)
          - text: |
              export OPENAI_API_KEY="sk-4f1c9d2e8b7a6305abcd"
            url: "https://example.com/triage/config.env"
            source_type: manual
            file_path: config.env
    proxy: direct
    retry: 0

classify:
  builtin: false                     # 凭证/曝面情报不走七大资讯类词典

dedup:
  key: "{url}"

push:
  - channel: stdout                  # 私有情报不出本机
```

`credhunt` lane 的 `github_tokens` 是凭据引用列表(如
`keychain:myia/credhunter/github-token`),配 `checkpoint_file` 落盘跨 run
续传——完整模板样例(脱敏,结构同真件)见
`config/credentials.example.yaml`(随源码发布,本门面仓不含)
(原生猎取 + 可选 aipocket 远程聚合双源)与
`config/exposure.example.yaml`(随源码发布,本门面仓不含)。纯凭证类品类的 dedup 键
建议字段组合(`{provider}-{key_fingerprint}`,`{title}` 永不进键)。引擎本体
在 `src/myssia/engines/credhunter.py`,场景件在
`plugins/myssia-credhunter/`(适配器被宿主 compile+exec 动态加载,核心仓库
与插件零静态耦合;组件缺失时结构化报 `credhunter_adapter_missing` 并提示去
设置页安装)。已部署 aipocket 实例的远程聚合走姊妹件 `myssia-credentials`
(remote 分级,endpoint + token 写品类 `plugin:` 节),与本件共存互不替代
(aipocket 融合线交付面)。

## 2. 凭据引用与空态

三条 lane 的凭据(`github_tokens` / `fofa_apikey` / `shodan_apikey`)只写
`env:` / `keychain:` 引用,fetch 期由宿主解析成值后**经参数注入**适配器——
适配器自身不读钥匙链、不读环境变量、不落盘。规范名空间 `myia/credhunter/*`:

```bash
myssia secret set myia/credhunter/github-token < token.txt
myssia secret set myia/credhunter/fofa-key < fofa.txt
myssia secret set myia/credhunter/shodan-key < shodan.txt
```

**未配 key = 显式空态,不是错误**(引擎面 AC6 口径):该源本轮
`credential_missing` skip、零请求、不拦品类,写好引用即恢复产出。细一点:

- `credhunt` 无可用 token = 该源本轮**不启用**(上游「无 token 根本不注册」
  语义);token 池里坏一个引用不废整池(留 warning 跳过,其余照用);
- `exposure` 的 FOFA/Shodan 双 lane 各自独立降级——只配了 FOFA key 就只跑
  FOFA 泳道,双缺才整体空态;
- 「显式直跑」的 CLI `myssia credhunt` 面才把 `tokens_missing` 当配置错误
  退 1(引擎面合同不变);
- 配错不用猜:`myssia doctor --json` 逐个核验引用是否存在,缺失即给出修复
  动作(补一句 `myssia secret set ...`)。

## 3. 本地密钥库:猎 → 存 → 验

猎手产 items 时全文密钥经掩码后即丢弃,「猎 → 存 → 验」由**插件侧密钥库**
补全:命中即入库(指纹 → 全文)→ credcheck 读库探测 → `check_state` /
`last_check` 回填。

- **位置与权限**:`$MYIA_HOME/credhunter-keystore.json`(无该环境变量时落
  当前工作目录);文件权限 **600**,原子写(同目录 tmp + rename,崩溃不留
  半份 JSON);首次命中才建文件。
- **内容形状**:`{"version": 1, "records": {指纹: {"apikey"(全文),
  "provider", "first_seen", "last_seen", "hit_count", "sources"(证据 URL
  去重,上限 20), "last_check", "check_state"}}}`;指纹 = sha256 前 16 hex。
- **掩码规则**(items / 推送模板 / CLI stdout 一律此形态):长度 > 12 取
  前 8 + `…MASKED…` + 后 4;9-12 只前 8;≤8 只前 2。**全文只存在于本机
  密钥库**——库属数据域,不出本机、不入 git、不外发。`exposure` 的曝面
  命中(无凭据)不入库。

验证/余额走 CLI 子命令 `myssia credcheck` 双入口(不走引擎面):

```bash
myssia credcheck --from-keystore --balance --json   # 读密钥库逐条探测并回填
myssia credcheck --apikey "sk-..." --balance --json # 显式传键(自备 key 冒烟)
```

`check_state` 三态:`final_verified`(已验证存活)/ `rejected`(已判死)/
`transient`(暂不可判);验证串行、每供应商 RPM ≤ 30(间隔 2s)、单次探测
超时 15s——不滥用任何发现的凭据。

## 4. 桌面端凭证猎手面板

侧栏「配置」组 →「凭证猎手」(路由 `/credhunter`),数据面只有一个协议
方法 `credhunter.keys.list`(空参):本地密钥库**纯文件读**——零网络、
零写入、库缺绝不建档;应答记录只带服务端算好的 `masked_apikey`,全文
`apikey` 在 handler 投影时丢弃,协议流与 UI 零全文密钥。错误族
`keystore_unreadable`(IO 不可读)/ `keystore_invalid`(坏 JSON/结构不符)。

面板是 V1 只读视图:

- **两个视图**:「全部密钥」(缺省全量)与「高危」——高危判据 =
  `check_state == final_verified`(已验证存活 = 泄漏且有效,最高活险;
  已判死/未定不入选);
- **六列表格**:密钥(掩码 + 指纹)/ 供应商 / 验证状态徽章(已验证存活 /
  已判死 / 暂不可判 / 未验证)/ 命中(次数 + 首末发现日期)/ 证据(行展开
  看证据 URL 清单)/ 最近检查;搜索(掩码/供应商)、供应商 chips 过滤、
  按命中数/最近发现排序,列宽可拖拽(本地持久化);
- **三条红线(G11,永久不做)**:无导出、无全文 reveal/复制、不拿猎得键
  实测对话——本屏不含任何复制/查看动作;
- **空态路径**:库未建(猎手尚未运行或尚无命中)→「凭证猎手还没有密钥库,
  可在品类 YAML 启用 engine: credhunter 源后跑一轮采集」;库在但零记录 →
  「密钥库还没有记录」;高危视图为空 →「没有已验证存活的泄漏凭证」。

## 5. 缺省参数与礼貌面

| 参数 | 缺省 | 说明 |
|---|---|---|
| GitHub code 搜索分页 | ≤5 页 × 100 条/页 | 返回数 < per_page 提前停页 |
| GitHub 查询预算 | 12 查询/run | checkpoint 游标跨 run 轮转 |
| GitHub 限速等待上限 | 90s | 仅 403/429 触发退避,只重试一轮 |
| GitHub blob 尺寸上限 | 1 MiB | 超限弃用,无截断降级 |
| credcheck 并发 | 串行,每供应商 RPM ≤ 30(间隔 2s) | 不抄上游并发 |
| 探测超时 | 15s | 验证/余额单次请求 |
| FOFA | 页大小 100、≤10 页、页间 0.3s、24 查询/run | base 可配(官方/代理部署者自决) |
| Shodan | 页大小 100、≤10 页、页间 1.0s、16 查询/run | — |
| 被动探测 | 仅 L0 unauth_read、每目标 12 请求预算 | 高危类 fail-closed 不开 |
| 掩码 | 前 8 后 4 | items/推送/CLI 一律掩码;全文只进本机密钥库 |

## 红线与边界

- **授权定位(硬边界)**:仅用于已授权安全研究与自有/已授权资产的泄露
  凭证排查;不做大规模扫描、不打高频探测、不滥用任何发现的凭据。被动
  探测只做 L0 未授权读;weak_password / idor / ssrf / sqli / rce 级探测
  fail-closed 不开,授权范围机制留二期。
- **私有情报红线**:命中物不入 git、不外发;items 与一切推送模板一律
  前 8 后 4 掩码,全文密钥永不进模板上下文;本类品类模板默认 push `stdout`
  (要外发先改模板掩码策略)。
- **许可边界**:上游参考对象为 AGPL-3.0 项目,本插件是其行为的**功能
  重实现**——零上游代码/标识符/文案复制,全部代码与数据由本仓从零创作
  (同 RSSHub/Firecrawl 服务消费先例的干净边界)。
- **面板只展示不导出**:凭证猎手面板永久不做导出/全文回显/实测对话
  (G11 三不借);验证触发、余额持久化等交互属后续件,不在 V1。
