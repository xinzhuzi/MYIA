# 快速上手

> 预发布说明:以下为目标 v0.1 体验,当前骨架中多数命令为桩实现。

## 1. 安装

```bash
pip install myia
```

## 2. 配置凭据

MYIA 不允许在 YAML 中写明文凭据,只能引用:

- `env:VAR_NAME` —— 运行时从环境变量读取
- `keychain:name` —— 从系统钥匙链读取(macOS Keychain / Windows DPAPI)

典型配置:用于精评的 LLM key 与推送通道目标:

```bash
export OPENAI_API_KEY=...   # 或任何 OpenAI 兼容端点
export FEISHU_CHAT_ID=...   # feishu_card 推送目标
```

## 3. 跑第一个插件

```bash
myia run plugins/wool.yaml
```

也可以直接对 AI 说需求——agent 会读[插件开发指南](../write-a-plugin.md)
现场生成品类 YAML(见 [skill/SKILL.md](../../skill/SKILL.md))。

## 4. 数据在哪

所有数据都在一个 SQLite 单文件里(默认 `myia.sqlite`):抓取条目、去重注册表、
变更基线、推送反馈与运行历史。在插件 YAML 的 `storage.retention` 设置保留期,
过期条目自动清理。
