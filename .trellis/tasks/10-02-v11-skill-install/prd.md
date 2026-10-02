# myia skill install:打通 Agent Skill 最后一公里

## 来源

2026-10-02 主人质询 skill/ 目录「没什么作用」——诊断正确:SKILL.md(432 行,22 个一致性测试锁与 schema.py 不漂移)写好了,但**没有任何安装通路**,价值只在被 agent 加载时兑现,而没人能把装进 Claude Code/Cursor 的技能目录。

## Requirements

- 新增 CLI 子命令 `myia skill`:
  - `myia skill install [--path <dir>] [--agent <claude|cursor|zcode|...>]`:默认探测本机常见技能目录(~/.claude/skills/myia/ 等),复制(或符号链接,--link)SKILL.md;--path 自定义;目标已存在时 --force 覆盖,默认结构化提示
  - `myia skill path`:打印 SKILL.md 源位置与各 agent 的推荐安装路径(--json)
- 结构化输出与退出码契约(0/1);安装结果不影响核心流水线(纯文件操作)
- README/docs 一行安装示例(如 `uvx myia skill install --agent claude`)
- 测试:临时目录为目标的安装/覆盖/已存在拒绝/--json 契约(不碰真实家目录)

## Acceptance Criteria

- [ ] 四个子行为有单测;--json 可被 jq 解析
- [ ] 在本机真实装一份到主人使用的 agent 环境(执行时询问主人选哪个)并演示:装完后该 agent 能按 SKILL.md 写出可加载的品类 YAML(myia test 通过)
- [ ] README 双语快速开始补一行;SKILL.md 自述安装方式

## Notes

- 这是 AI-NATIVE 第一发行形态的兑现动作;与 docs 双受众设计(四同步纪律)不冲突
