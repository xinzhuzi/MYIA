# v112 第二切片门禁证据(B2/B3/B4/C10/C12 + E4/E5;2026-10-03 21:01 CST)

## 门禁(显式退出码)

```
$ cd desktop/ui-src && npm test -- --run
Test Files  20 passed (20)
      Tests  211 passed (211)   # 含本批新增 4 文件 30 用例(global-run 6 / feed-card-feedback 6 / trend-card 11 / settings B3 8);vision 线 feed-screen/vision-models 测试同期转绿
exit 0(vitest 自身通过;tail 无 FAIL 行)

$ cd desktop/ui-src && npx tsc -b
(零输出)
exit 0

$ cd desktop/src-tauri && cargo check
Finished `dev` profile [unoptimized + debuginfo] target(s) in 0.12s
CARGO_EXIT:0   # C10 MYIA_APP_VERSION 注入后复检

$ uv run --no-sync python -m pytest tests/test_desktop_sidecar_protocol.py -q
98 passed in 6.90s
exit 0

$ uv run --no-sync python -m pytest tests/test_feedback.py tests/test_store_dedup.py -q   # sqlite.py/models.py 触达面加测
122 passed in 1.02s
exit 0
```

## 协议对账(spec 变更纪律第 2 条;2026-10-03 21:00 实测)

```
data.allowed(= sorted(_HANDLERS)) count = 41
本批四方法在列: feedback.mark / feedback.list / feedback.stats / store.trend
spec 注册表(.trellis/spec/desktop/sidecar-protocol.md)= 35 行,全部 ⊆ allowed
  (差额 6 = vision-v2 image.models.*×4 + image.server.*×2,已注记归彼线随注,勿代注)
client.ts api 门面键数 = 21(核心 10 + v112 批 8 + feed-ux 批 3)
  本批四封装在列: feedbackMark / feedbackList / feedbackStats / storeTrend
types.ts SidecarProtocol 盖 23(含本批 4 + version.app_version 扩展)
```

## PROTOCOL_VERSION 处置

- 开工核得现值 = 5(vision-v2 批 bump);按 design §1.3 不二次 bump,
  CHANGELOG 在 Unreleased 补记本批方法行(附「rides the existing ledger」说明)。

## 冲突注记(并行会话)

- vision 线 20:33-20:50 活跃写 entry.py(image.models/server 六方法,未提交)
  与 vision-form.tsx(431 行未提交)/vision-models.test.tsx:本批 entry.py 追加前
  stat mtime=20:33:32(距动手 >20 分钟,非活跃)+ ast.parse 通过后,以最小增量
  追加(import + 4 handler + _HANDLERS 4 行 + docstring 行),零触碰其 hunks。
- 其测试 test_method_registry_allowed_matches_handlers 的 37 计数随本批 +4 改 41
  (注册表纪律要求计数如实;docstring 补「v1.1.2 批第二切片」行)。
- test_version_roundtrip(本批首切片用例)随 C10 app_version 扩展更新期望形状。
- 期间 vision 线自身转绿:feed-screen「v2 图析详情」测试与 logs/api.ts tsc 错误
  由彼线在 20:50-21:00 间修复(本批未代改)。
