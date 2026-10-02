# v1.0 立项开源:双语文档 + demo + 独立包 + 发布

## Goal

发布就绪(规划第六/九节):双语文档、3 分钟上手动图/视频、myia-classifier 独立包、社区首发。**前置硬条件:v0.1-v0.4 全部完成。**

## 发布形态(2026-10-01 grill Q11 定案)

- v1.0 发布三形态:**CLI(PyPI)+ Docker(compose)+ Agent Skill**
- **桌面客户端正式版(Tauri 壳+五大界面+updater)与 Web 面板正式版 = v1.1+**:待 v02-desktop-spike 实测结论后细化任务树;v0.x 阶段桌面唯一交付是 spike

## 子任务地图

| # | 子任务 | 边界 |
|---|---|---|
| 1 | v10-docs-bilingual | zh/en 文档站 |
| 2 | v10-demo | README 动图 + 视频 |
| 3 | v10-classifier-pypi | myia-classifier 独立 pip 包 |
| 4 | v10-release | README 打磨 + 社区首发 |

## 阶段验收标准

- [ ] 新人按文档 30 分钟内跑通任一品类(找一位真实新手验证)
- [ ] README 3 分钟上手动图可见;视频可播放
- [ ] PyPI 发布 myia 主包(`pip install myia` 可用)与 myia-classifier 独立包
- [ ] 首发帖发出(Reddit r/selfhosted / V2EX / 即刻 / LinuxDo),issue/PR 模板与 CONTRIBUTING 就绪

## 发布检查

- [ ] 全仓库密钥扫描(明文凭据零容忍)
- [ ] LICENSE=**MIT**(2026-10-01 grill Q9 确认)+ 三方依赖许可清单(全 MIT/Apache/BSD,规划底线)
- [ ] 仓库内无私有系统痕迹(私有系统名(见 LOCAL-NOTES)/生产语料/内网地址)——逐目录检查(.trellis/ 含内,grill Q8:仓库即公开)
