# Implement:messaging-ui

前置:core 已收口(task.json 状态 in_progress 后动工);feishu/telegram 未落地时以空目录开发,端到端演示等它们收口。

## 步骤

1. **sidecar 四端点 + 协议测试**
   - `channels.list/refresh/alias` + `push.write`(照 sources.write 范式:校验门+原子写);`_HANDLERS` 注册
   - 协议测试:正例×4 + push.write 拒写坏 targets(文件未变断言)+ refresh 未知平台
2. **api.ts + 屏幕 + 路由**
   - screens/messaging/ 三件 + routes/messaging.tsx + App.tsx 注册;两区布局(目录/规则面板)
   - 组件测试:渲染/别名编辑/targets 多选与保存/空态断连态
3. **真机冒烟**
   - Tauri dev:目录渲染真实数据(feishu 3 会话对照集)→ 别名编辑生效 → 勾选 targets 保存 → YAML 变更 → run 端到端收卡
4. **收尾门禁**
   - `python -m pytest tests/ -q` 全量绿(协议测试含)
   - desktop UI 组件测试绿(命令以 desktop/package.json scripts 为准)

## 验证命令

```bash
python -m pytest tests/ -q
python -m pytest tests/test_desktop_sidecar_protocol.py -q
# desktop UI 测试:见 desktop/package.json scripts(npm test 族)
```

## 回滚点

- 步骤 1 集中在 desktop/entry.py + 协议测试,步骤 2 全新文件+两处注册行,各自独立 revert
