# 发布:README 打磨 + 社区首发

## Goal

开源运营第一步(规划第九节):卖点文案落地,首发 Reddit r/selfhosted / V2EX / 即刻 / LinuxDo。

## Requirements

- README 打磨:第一句 "AI-native intelligence hub——说需求,AI 做其余。";空白点对比表(规划第一节);模仿致谢区(引用的开源项目致谢,合规加分)
- GitHub 门面:Topics/About/ Social preview、issue 模板、PR 模板、CONTRIBUTING、SECURITY(凭据处理策略)
- 发布物料:三平台各一版文案(zh/en 分场景);发布节奏主人定
- 私有信息终检:全仓库扫描(私有系统名(见 LOCAL-NOTES,仅本地)/语料/内网地址/密钥)

## Acceptance Criteria

- [ ] 密钥与私有信息扫描报告零命中
- [ ] 模板与 CONTRIBUTING 就绪,首个 issue 有响应流程
- [ ] 帖子发出(截图/链接记任务日志),首周反馈汇总

## Notes

- 名字=**MYIA**、LICENSE=**MIT**(2026-10-01 grill Q9 确认,与 pyproject 一致);ScoopHub/InfoForge 备选作废

> **2026-10-03 grill Q4 重开注记**:本档未勾验收项(密钥扫描零命中 / CONTRIBUTING
> 与首 issue 流程 / 发帖+首周反馈)移交 `10-03-v111-release` 执行;发布完成后回本档终勾。
> 决议来源:`10-03-grill-v112`。

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: docs/launch/RELEASE.md:110 现写 'myia --version # 预期输出:myia <刚发布的版本号>(与上一步 PyPI 页面所示一致)',硬编码版本已改占位符;grep '0\.1\.0' docs/launch 下零命中;移交项经核仍被 10-03-v111-release 跟踪(prd.md:40 密钥扫描、42-43/76 四帖素材与链接回填)。

遗留(需主人手动完成):
- 全仓密钥/私有信息扫描(gitleaks 或等价)+命令与报告入任务日志——由 in_progress 的 10-03-v111-release 执行(prd.md:40,73),目前仓库内仍无该报告(需真实执行,不得造假)
- 四帖(Reddit/V2EX/即刻/LinuxDo)由主人定稿并实际发出(docs/launch/README.md 首周反馈表仍为空表,未发出),链接/截图回填本档并按模板汇总首周反馈(发布+真人运营,需主人)
- 发布完成后回本档终勾三验收项(10-01-v10-release/task.json 既定流程)
