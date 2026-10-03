# 消息平台:桌面端消息屏(目录浏览+规则挑对象)

## Goal

MYIA 桌面端(Tauri+React)新增第 6 屏「消息」:通道目录浏览、别名编辑、给 push 规则挑具体推送对象。前置:messaging-core/feishu/telegram 已落地。

**前置分层**:core 收口后即可开发端点与屏幕(feishu/telegram 未落地时目录空态合法);「目录有真实数据、端到端可演示」依赖 feishu/telegram 收口。

## Requirements

1. **「消息」屏**(screens/ 第六屏,routes/ 同风格):
   - 平台分组的通道列表(名称/类型/最后发现时间/死信状态徽标)
   - 刷新按钮(触发各平台 `discover_directory`)、别名行内编辑(写 channel_aliases.json 语义)
   - 空态(目录为空:指引先配置平台凭据)、断连态(sidecar 不可达)与现有屏一致
2. **规则挑对象**(grill Q8 定案:完整写回):消息屏内为每条 push 规则提供 targets 选择器——从通道目录多选,产出 `platform:名称` 列表,经 `push.write` 端点写回 YAML(settings 屏维持只管凭据的现状;事实轮已核其无任何 YAML 写路径)。
3. **sidecar 端点**(desktop/entry.py,domain.verb 小写点分风格,事实轮已核 `_HANDLERS` @ entry.py:911-921):新增 `channels.list` / `channels.refresh` / `channels.alias`(set/delete 由 payload 区分)/ `push.write`(照 `sources.write` 范式 @ entry.py:523-704:过 schema 校验门 + 原子写);协议测试进 tests/test_desktop_sidecar_protocol.py(行分隔 JSON-RPC、rpc() 夹具范式)。
4. **导航**:App 路由注册第 6 屏,命名直白(「消息」),不用行话。

## Acceptance Criteria

- [ ] 屏幕组件测试(目录渲染/别名编辑/目标选择器),协议测试进 test_desktop_sidecar_protocol.py(含 `push.write` 校验拒写与原子写回环)。
- [ ] 真机冒烟:消息屏看到飞书 3 会话 + telegram 会话;给一条规则勾选对象保存,run 后目标群收到推送。
- [ ] 断连/空态不白屏,与现有屏错误范式一致。

## 非目标

- 移动端/Web 端
- 消息内容回看(出站-only,无会话记录可看)
- W2/W3 平台的 UI 特化(平台多了自然分组,不需要特化)
