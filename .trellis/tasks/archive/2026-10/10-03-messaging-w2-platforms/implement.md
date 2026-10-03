# Implement:messaging-w2-platforms

前置:task.json 状态改 in_progress 后动工;执行等主人令(grill round-2 Q1)。每步聚焦自测全绿进下一步。

## 步骤

1. **ntfy 适配器 + 单测**(最小最独立,先打样):POST/鉴权头/4096 截断/X-Markdown/直达寻址/错误分类。
2. **钉钉适配器 + 单测**:裸 webhook 路径 → 加签路径(secret 配置时);errcode 分类。
3. **企微适配器 + 单测**:token 缓存/过期/40001/42001 重试;2048B 分块;touser 寻址。
4. **注册与 schema**:CHANNELS/PLATFORMS 三行、PushChannel 枚举、同平台映射表;黄金回归仍绿。
5. **目录语义与 CLI**:DirectoryDiscoverUnsupported + channels refresh 三家说明文案 + 单测。
6. **UI 转实装**:三平台卡头像/指南/状态派生接入(照 hermes-look 框架);vitest 更新。
7. **收尾门禁**:`uv run --no-sync python -m pytest tests/ -q` 本域零失败 + vitest 全绿;真机冒烟清单(每平台一条)回填本档。

## 真机冒烟清单(2026-10-03 回填;执行等主人令——grill round-2 Q1)

每平台一条最小路径,全部是纯出站、零真实凭据入仓(引用名可留档,值不入仓):

1. **ntfy**:`export NTFY_TARGET=https://ntfy.sh/<自选topic>` → 手机装 ntfy App(或开
   ntfy.sh 网页)订阅同名 topic → 品类 YAML `push: [{channel: ntfy, target: env:NTFY_TARGET}]`
   → `shishi run <yaml>` → 手机收到一条「📡 …日报」通知。
2. **钉钉**:群设置 → 机器人 → 添加自定义机器人(安全设置选加签)→
   `export DINGTALK_WEBHOOK_URL=<webhook>`、secret 存 env → YAML
   `push: [{channel: dingtalk, target: env:DINGTALK_WEBHOOK_URL, dingtalk_secret: env:<名>}]`
   → `shishi run <yaml>` → 群里收到 text 消息(验证加签增量路)。
3. **企微**:管理后台建自建应用 → `export WECOM_CORPID/WECOM_CORPSECRET/WECOM_AGENTID/
   WECOM_TUSER=<成员userid>` → YAML `push: [{channel: wecom, target: env:WECOM_TUSER}]`
   → `shishi run <yaml>` → 该成员应用会话收到 text 私聊;二跑确认
   `wecom_token_cache.json` 落数据根且不再 gettoken。

## 验证命令

```bash
python -m pytest tests/ -q -k "ntfy or dingtalk or wecom"
npm --prefix desktop/ui-src run test
```

(uv run --no-sync 前缀照项目惯例)

## 回滚点

- 步骤 1-3 各自独立新文件,单文件 revert;步骤 4-6 触碰注册/schema/UI,独立 hunk revert
