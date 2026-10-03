# 发布物料(仅内部草稿,**不自动发出**)

> 首发平台:Reddit r/selfhosted / V2EX / 即刻 / LinuxDo,每平台一版文案,
> 语言按场景分配(Reddit 英文,其余中文)。**发布节奏由主人定**;本目录只是
> 弹药库,发出后把链接/截图记进任务日志(`.trellis/tasks/10-01-v10-release/`)。

## 文件索引

| 文件 | 平台 | 语言 | 场景要点 |
|---|---|---|---|
| [reddit-r-selfhosted.md](reddit-r-selfhosted.md) | Reddit r/selfhosted | en | 讲「self-hosted 情报中枢」故事,忌纯营销;附 zh 对照草稿 |
| [v2ex.md](v2ex.md) | V2EX | zh | 「分享创造」第一手复盘口吻,技术细节为主 |
| [jike.md](jike.md) | 即刻 | zh | 短文案 + 动图,轻量口语化,带话题标签 |
| [linuxdo.md](linuxdo.md) | LinuxDo | zh | 社区偏好真诚分享 + 自部署细节,注意版规与信任等级 |

## 发布前统一检查(每篇都过一遍)

- [ ] **零凭据**:截图/录屏/文案里不得出现任何 token、Cookie、内网地址、真实姓名
- [ ] 仓库链接、docs 链接、LICENSE(MIT)标注齐全;状态如实标注 alpha,不夸大成熟度
- [ ] 采集伦理说清楚:默认尊重 robots.txt、限速礼貌,「真人验证+手机号」类源不碰(FAQ 有专节)
- [ ] demo 动图已就位(`docs/demo/`,任务 10-01-v10-demo),且画面已脱敏
- [ ] PyPI 包若尚未发布,安装命令统一用源码安装(git clone + uv sync),不写不可用的 `pip install shishi`

## 发出后:首周反馈汇总(验收项)

发出当天建下表,每天追加一行;一周后汇总成「首周反馈」记回任务日志:

| 日期 | 平台 | 链接 | 浏览/赞 | 评论要点 | 待办 issue |
|---|---|---|---|---|---|
| | | | | | |

反馈处理原则:bug → 建 issue 走模板;功能建议 → 按 CONTRIBUTING 的响应流程
(7 天内首次回应);负面反馈里关于采集伦理的部分优先回应(这是项目的红线卖点)。

## 贴文里展示的最小品类(零凭据、可复制运行)

各平台文案都会强调「一个品类就是一个 YAML」,下面这份是最小演示配置
(与 `docs/en/getting-started.md` 示例同构,直接可跑):

```yaml
id: demo-min
name: Minimal demo
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
classify:
  builtin: false                 # demo items match no category; disable the filter
push:
  - channel: stdout              # zero-credential local verification
```

三个命令走完「验证 → 演练 → 正式」:`shishi test` / `shishi run --dry-run` /
`shishi run`(详见双语 Quickstart)。
