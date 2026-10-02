# CLI 基础:myia run

## Goal

CLI 是 AI 的手柄(AI-NATIVE):v0.1 先做 `myia run`,把单品类流水线串通;输出对 agent 友好。

## Requirements

- `myia run <yaml路径>`:加载插件 → 跑一次流水线;`--once`(默认)与 `--loop`(常驻)
- `--dry-run`:全链执行但不推送,结果结构化打印(将要推的条目 + route 判定 + skip 原因)
- `--json`:机器可读输出(AI 消费路径,与人类可读输出并存)
- 退出码语义:0 成功 / 1 配置错误(schema 拒载、明文凭据)/ 2 采集全部失败 / 3 部分失败——供 agent 与 CI 判断
- `myia --version`;`list`/`test`/`init` 等子命令留位(v0.2 实现)

## Acceptance Criteria

- [ ] `myia run plugins/stocks.yaml --dry-run --json` 输出结构化且可被 `jq` 解析
- [ ] 配置错误时退出码 1,错误信息含字段路径(与 schema 层打通)
- [ ] `--help` 输出清晰(文档也是 AI 的输入)

## Notes

- 填充 `src/myia/cli.py`(46 行壳);框架选型(typer/argparse)design.md 定,倾向轻量
- `pyproject.toml` 的 console_scripts 入口本任务验证
