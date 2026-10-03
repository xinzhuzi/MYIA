# 存储加固:retention / VACUUM / 断点续跑

## Goal

桌面长期运行防膨胀(规划 v1.7 storage 节)与 run 级可靠性。

## Requirements

- `storage.retention`(如 90d):items 与过期基线定期清理;清理任务随调度周期执行
- `storage.vacuum: monthly`:SQLite VACUUM 定时执行
- 断点续跑:runs 表记录步骤进度;失败 run 重跑时已完成步骤不重复(去重注册表天然兜底重发,此处避免重复抓取与重复分类开销)
- 库文件损坏/迁移检查:启动时 schema 版本校验,不匹配给结构化错误

## Acceptance Criteria

- [ ] 过期清理与 VACUUM 单测(内存库+临时文件库)
- [ ] 中断恢复:kill 后重跑,无重复推送,日志可见跳过
- [ ] storage 节缺省值(90d/monthly)单测

## Notes

- 填充 `src/myia/store/`;清理策略要区分「已推送」与「未推送」条目

## 验收记录(2026-10-03,受主人委托代验)

verdict: accepted

evidence: uv run --no-sync python -m pytest tests/test_storage_hardening.py -q → 47 passed;三条 AC 对位测试齐全:test_cleanup_*/test_maybe_vacuum_*/test_vacuum_rebuilds_file_and_keeps_data(tmp_path)(清理+VACUUM,内存库+临时文件库)、test_resume_after_kill_skips_completed_stages(断言零重复抓取+『断点续跑』in caplog 日志可见跳过)与 test_resume_same_slot_records_no_duplicate_push(无重复推送)、test_storage_section_defaults_90d_monthly(断言 retention=='90d'、vacuum=='monthly');schema 版本校验见 src/myia/store/sqlite.py:505-558。
