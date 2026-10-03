# AI 任务分发模板(v1.1,可直接粘贴给编码 AI)

> 2026-10-02 依主人指示建档。教训:外部建议可能基于过期快照——**分发前先让 AI 实跑第 0 步核对基线**,以仓库实况为准。
> 使用:复制下方模板,按需裁剪任务清单;任务边界以各 prd.md 为准,本模板只管协议与顺序。

## 模板正文

```
继续 MYIA v1.1,工作目录 ~/Project/Github/MYIA。

## 第 0 步:基线核对(必做,不许跳过)
- git status 必须干净;不干净先停下来报告,不要在半成品上动工
- uv run --no-sync python -m pytest -q --tb=no 必须全绿(建档时基线:1217 passed / 14 skipped)
- 任何与预期不符(比如听说的失败测试不存在/已修),以实跑结果为准并报告

## 任务清单(按 .trellis/tasks/10-02-v11-umbrella/prd.md 的顺序,先读各任务 prd.md 再动手)
1. 10-02-v11-desktop-app(P1 头号:桌面正式版)
2. 10-02-v11-plugins-source-arch(P1:插件源码化转向,osint 样板先行)
3. 10-02-v11-skill-install / 10-02-v11-repo-hygiene(P2,可与上面并行)
4. 10-02-v11-low-* 六子任务(逐条清偿;low-plugins 的 README 项并入任务 2)
5. 10-02-v11-low-release(最后:与 ci.yml 对齐确认)

## 系统礼仪铁律(2026-10-03 主人指示,全文见 spec/domain/os-etiquette.md)

- 严禁任何抢前台命令(open -a 默认激活、osascript activate、全屏截图);拉起 App 一律 open -g / --hide 或不拉起;验证用 pgrep/二进制直跑/文件实查;截图只许目标 App 窗口级,拿不到就写「需主人自验」

## 每任务执行协议(铁律)
1. 动手前:完整读该任务 prd.md + implement.jsonl 列的上下文文件
2. 实现:遵守 .trellis/spec/(结构化错误/退出码/公开红线)
3. 验证:prd.md 的 Acceptance Criteria 逐条**实跑**,不许只看代码就说通过
4. 门禁:全量 pytest 真绿;测试或构建失败不许跳过、不许删测试保绿
5. 归档:trellis 任务状态推进(写执行记录→review;主人验收后 archive)
6. 提交:单条 git 提交(英文祈使句,说清做了什么+验证了什么),push
7. 凭据:只动 env:/keychain: 引用,真实值绝不入仓库
```

## 与既有规范的关系

- 「不许删测试保绿」「pytest 全绿才可并」→ spec/python/quality.md(已有)
- 凭据红线 → spec/domain/security-baseline.md(已有)
- 本模板只新增:第 0 步基线核对、每任务七步协议、分发用任务顺序
