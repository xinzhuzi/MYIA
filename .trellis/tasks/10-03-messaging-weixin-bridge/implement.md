# Implement:messaging-weixin-bridge

前置(task.json 改 in_progress 前先核):PRD 冲突裁定注记仍适用——等
`src/myia/schema.py` 在途改净 + messaging 域线(platforms/W2)收尾后再开工,
同屏后动者 rebase。每步聚焦自测全绿进下一步;红线全程:不 vendor Hermes
代码、subprocess/httpx 之外零新依赖(本通道实际纯 stdlib)、入站零出现。

## 步骤

1. **桥接通道核心 + 单测**(新文件 `src/myia/push/weixin.py` + 
   `tests/test_messaging_weixin_bridge.py`,照 ntfy/W2 打样先例):
   `WeixinChannel`(D1 形态,runner 注入点)+ `probe_bridge` +
   错误映射表(D3 全行)+ `DEFAULT_BRIDGE_TIMEOUT_SECONDS`。
   - 聚焦自测:`uv run --no-sync python -m pytest tests/test_messaging_weixin_bridge.py -q`
     全绿。用例覆盖 = AC1 四态(mock runner:成功 exit 0+`{"success":true}`;
     Hermes 缺失 → `bridge_unavailable` 带修复指引;token 过期
     `ret=-14` → `session_not_ready`;-2 stale-session `session not ready`
     文案 → `session_not_ready` 带指引)+ 增补:exit 2 →
     `bridge_usage_error`、超时 → `bridge_timeout`、非法 JSON →
     `weixin_send_error`、`HTTP 404` 归一化不撞死信、
     **上表每行错误过 `classify_dead_error` 断言 None(零死信)**、
     `parse_direct_ref`(`xxx@im.wechat`/`@chatroom` 命中、中文别名落目录)、
     `discover_directory` 抛 `DirectoryDiscoverUnsupported`、
     legacy target 引用解析(`env:WEIXIN_PEER_ID`)。
   - 回滚点:独立双新文件,整文件 revert 即净。

2. **注册与 schema**(`src/myia/push/__init__.py` 两表 +
   `src/myia/schema.py` 枚举/字段/宿主表 + `src/myia/pipeline.py`
   `_W2_CHANNEL_FIELD_KWARGS`,均一两行 hunk):
   - 聚焦自测:`uv run --no-sync python -m pytest tests/test_push_schema_targets.py tests/test_messaging_pipeline.py tests/test_push.py -q`
     全绿 + 既有 golden 回归零漂移(不配 weixin = 逐字节等价);
     新增用例:PushChannel Literal 收 `"weixin"`、同平台约束
     (`weixin:` 前缀 targets 合法、他平台拒绝)、`weixin_hermes_bin`
     仅 weixin 通道可配(别处配即 `SchemaValueError`)、
     `_build_channel` 把 bin 路径下传(断言构造 kwargs)。
   - 回滚点:四个文件各自独立 hunk,`git checkout -p` 逐 hunk 回。

3. **sidecar `bridge.status`**(`desktop/entry.py` `_HANDLERS` #28 +
   `tests/test_desktop_sidecar_protocol.py` 契约用例 +
   `.trellis/spec/desktop/sidecar-protocol.md` 注册表镜像同步):
   - 聚焦自测:`uv run --no-sync python -m pytest tests/test_desktop_sidecar_protocol.py -q`
     全绿。用例:应答含 `available/reason/fix_hint/bin_found/
     weixin_configured/gateway_alive`;用临时目录伪造成/伪缺失三种
     probe 形态断言派生正确;`method_not_found` 应答 `data.allowed`
     含新方法名(对账纪律 2)。
   - 回滚点:entry.py 单 handler + `_HANDLERS` 一行,独立 hunk 回。

4. **UI 灰卡披露**(`screens/messaging/api.ts` `bridgeStatus()` 屏私有
   封装 + `platform-overview.tsx` 四态 + `messaging-screen.tsx` 接线 +
   vitest):
   - 聚焦自测:`npm --prefix desktop/ui-src run test` 全绿。用例 = AC2
     UI 侧:微信卡在 bridgeStatus `{available:false}` 时渲染灰态
     「需本机 Hermes」+ 披露文案(platform-detail 含「桥接」「Hermes」
     字样)、`{available:true}` 时绿态且目录速览走 manual 空桶文案、
     `buildPlatformCards` 不传 bridgeStatus 时其余平台卡快照不变
     (向后兼容)、凭据指南含 `send --list weixin` 自查步骤。
   - 回滚点:前端三文件独立 hunk 回(revert 后微信回落 UPCOMING 灰卡,
     不破坏其余平台)。

5. **无 Hermes 环境冒烟**(AC2 后端半):`MYIA_HOME=$(mktemp -d)` 沙箱 +
   `weixin_hermes_bin` 指向不存在路径:
   - 聚焦自测:品类 YAML 含 weixin push 条目 → `uv run --no-sync python -m myia run <yaml> --dry-run --json`
     配置装载零报错(dry-run 到 push 装配层);真发送路径单测级已覆盖
     `bridge_unavailable`(步骤 1),此处补一条端到端:非 dry run 后
     `pushes[]` 报告含 `[bridge_unavailable]` 且修复指引文案在错误串里。
   - 回滚点:无代码改动(纯验证步),零回滚需求。

6. **开源边界披露**(AC4):根 `README.md`(推送叙述段)+ 
   `docs/zh/getting-started.md` + `docs/en/getting-started.md` 各一句
   「微信通道依赖本机 Hermes(桥接,无 Hermes 则该平台不可用)」:
   - 聚焦自测:三文件 rg 披露句各命中;zh/en 语义对照一致
     (双语纪律);`uv run --no-sync python -m pytest tests/ -q` 仍全绿
     (防文档测试意外咬合)。
   - 回滚点:三个文档独立 hunk 回。

7. **收尾门禁 + 真机冒烟清单回填**(AC3,执行需主人机器 Hermes 常驻):
   - 全量门禁:`uv run --no-sync python -m pytest tests/ -q` 零失败 +
     `npm --prefix desktop/ui-src run test` 全绿;提交前
     `gitnexus detect-changes -r MYIA --scope staged`(AGENTS.md 纪律)。
  - 真机清单(回填本档,零凭据入仓):
    1. `~/.hermes/hermes-agent/.hermes/bin/hermes send --list weixin --json`
       记下目标 peer id(不入仓);
    2. 品类 YAML `push: [{channel: weixin, weixin_hermes_bin: ~/.hermes/hermes-agent/.hermes/bin/hermes}]`
       + 规则 `targets: [weixin:<peer>]` → `shishi run` → 微信收到一条卡片;
    3. 对端长期未回消息的冷发对象再发一次 → 得 `[session_not_ready]`
       结构化指引(「先让对方发条消息」)而非死信,且
       `delivery_ledger.json` 无该 peer 键(零死信实证);
    4. UI 消息屏:微信卡绿态(bridge.status available)与灰态
       (临时把 `weixin_hermes_bin` 指向空路径)各截图核对。

    **真机回填(2026-10-03,主人机器,Hermes 常驻实跑)**:
    1. ✅ `--list weixin --json` 命中唯一 DM peer(形如 `o9cq…@im.wechat`,
       全 id 不入仓);
    2. ✅ 真发送达:品类走缺省 bin(未配 `weixin_hermes_bin`)+ 定向 targets,
       `shishi run` exit 0 / status success / weixin 报告 ok=True 无错误,
       全程 2.2s(本地 HTTP 源 → Hermes → iLink);数据根无
       `delivery_ledger.json`(零死信);
    3. ◐ 半证:伪 peer 冷发(`coldprobe…@im.wechat`,Hermes 目录外)→
       Hermes 目标解析失败,MYIA 归 `[weixin_send_error]` 瞬态
       (D3 表末行语义),`delivery_ledger.json` 不存在(零死信实证);
       **真 token 过期的 `[session_not_ready]` 需真陈旧 peer(令牌自然
       过期后方可复现),主人侧可排**:任一长期未回消息的已解析 peer
       再发一次即验(单测面已按蓝本文案全覆盖该路径);
    4. ◐ 组件级已证(vitest:灰态「需本机 Hermes」与绿态「已连接」+
       manual 空桶文案三用例);真 GUI 截图属主人手工核对项,未跑。

## 验证命令

```bash
uv run --no-sync python -m pytest tests/ -q -k "weixin"
uv run --no-sync python -m pytest tests/ -q
npm --prefix desktop/ui-src run test
gitnexus detect-changes -r MYIA --scope staged
```

## 回滚点总表

| 步骤 | 触碰 | 回滚单位 |
|---|---|---|
| 1 | 新双文件 | 整文件 revert |
| 2 | push/__init__ + schema + pipeline 各小 hunk | 逐 hunk `git checkout -p` |
| 3 | entry.py + 协议 spec 镜像 | 逐 hunk(注册表一行 + handler) |
| 4 | ui-src 三文件 | 逐 hunk(回落 UPCOMING 灰卡不破功能) |
| 5 | 无代码 | — |
| 6 | 三文档 | 逐 hunk |
| 7 | 无代码(清单回填属本档) | — |

全量回滚 = revert 本任务全部 commit;不配 weixin 通道的用户在任何中间
状态都零感知(schema 枚举扩展对既有配置是纯放宽)。
