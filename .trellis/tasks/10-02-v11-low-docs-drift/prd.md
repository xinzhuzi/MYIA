# v1.1 docs:文档漂移清偿(6 条 low)

## Goal

清偿 low backlog(docs 模块,素材编号 1-6,来源 `.trellis/tasks/10-01-myia-v10-launch/research/v11-low-backlog.md`),消除文档与实际行为的漂移。

## Acceptance Criteria

- [ ] **1.** where: `skill/SKILL.md:368` — what: §4.3 把 `run --json` 的 stages[] 多列了 enrich;运行时实际五阶段为 fetch/classify/dedup/analyze/push,aggregate 仅在声明时插入。修法:改为五阶段,注明 aggregate 条件性插入。
- [ ] **2.** where: `docs/zh/schema.md:193` + `docs/en/schema.md:210` — what: 「各通道凭据约定见 write-a-plugin」悬空引用,目标页无该节(真实约定在 SKILL.md §2.13 与各通道缺省 env 名)。修法:write-a-plugin 补该节,或改指 SKILL.md §2.13。
- [ ] **3.** where: `docs/demo/README.md:55` — what: 「根 README 嵌入由发布工位完成」指示过时(README 已嵌入同一 gif,照做会重复)。修法:删除或改写该指示。
- [ ] **4.** where: `docs/demo/` — what: 视频成片仍为占位(B站/YouTube 空链)。修法:若本日装配出成片则更新链接;否则保留「需主人录一次」并记入任务 openIssues,不造假。
- [ ] **5.** where: `src/myia/enrich/prompt.py:4` — what: docstring 引旧路径 classify/data/keywords.json。修法:改为 `packages/myia-classifier/src/myia_classifier/data/keywords.json`。
- [ ] **6.** where: `packages/myia-classifier/README.md:58` — what: 自定义词表示例标题「便宜出极速云主机三台」在打包词表零命中(照跑得 None)。修法:改为可命中标题(如「便宜出极速服务器三台」→server)并实测。

## Notes

- 全部为文档/注释级修改,不动运行时行为(条目 6 需用打包词表实测命中)。
- 回归:`uv run --no-sync python -m pytest` 相关文档测试与全量绿。
