# 冻结清单与同源复现(10-03-golden-frozen-snapshots)

## 冻结副本(9 份,镜像黄金 JSON 键 = 仓库根相对路径)

PRD 立项时黄金集为七份;执行时点已因并行线收编 games/news 长成九份,
按"冻结黄金回归读到的一切"原则全量冻结。布局按黄金 JSON 键镜像落位
(`GOLDEN_DIR / key`),plugins/stocks.yaml 与 tests/fixtures/stocks.yaml
重名冲突由此天然消解(PRD 的 `<原名>.yaml` 平铺命名在两 stocks 并存时
必然撞名,镜像相对路径是其无歧义推广;决策记档于此)。

```
tests/fixtures/push_targets_golden/
├── plugins/
│   ├── ai-news.yaml        ├── games.yaml
│   ├── credentials.yaml    ├── gpu-prices.yaml
│   ├── monitor.yaml        ├── news.yaml
│   ├── stocks.yaml         └── wool.yaml
└── tests/fixtures/stocks.yaml
```

冻结时点:HEAD=dc1cf86,plugins/ 与 tests/fixtures/ 工作树净
(无并行在途改动混入)。

## 再生成入口(唯一通道,均显式)

- `uv run --no-sync python tests/regen_push_targets_golden.py`
  仅从冻结副本重算黄金 JSON(同源自检;不改冻结副本)。
- `uv run --no-sync python tests/regen_push_targets_golden.py --refreeze`
  先从活库重新冻结九份副本再重算 JSON(PRD 升级路径:漂移过大经评审
  重立基线时用)。黄金集增删 = 手改脚本内 MANIFEST 再跑,不自动扫描
  活库——"自动跟随活库"正是被治的病。

命令亦留档于测试 docstring(test_push_schema_targets.py::
TestGoldenRegression)与脚本模块 docstring。

## 同源可复现证明(PRD AC1)

1. `--refreeze` 首跑:九份副本落位 + JSON 重算,exit=0;
2. 不带 flag 复跑(纯从冻结副本重算),diff 首跑输出 → **逐字节一致**
   (IDEMPOTENT-OK);
3. 新 JSON 与提交前版本 diff:+122/−26,全部为 schema additive 空键
   (ntfy_token/dingtalk_secret/wecom_*/weixin_hermes_bin 等 None)与
   上次黄金同步(7b8fbad)以来 plugins 的合法非 push 演进
   (credhunter 引擎节、vision image: img@src、enrich 设置等),
   无任何 push 子树取值漂移——比对断言形态不变,旧承诺语义原样保持。

## 解耦机制(以后改 plugins push 配置为何不再误伤)

- 测试数据流改为:`GOLDEN_DIR/<键>` 冻结副本 → load_category_file →
  model_dump → push 子树 additive 等价比对。活体 plugins/*.yaml 从此
  **不在数据流里**。
- 并行会话给自己品类 yaml 加 push 条目/字段/改模板 = 只动活体,冻结
  副本与黄金 JSON 均不变 → 测试恒绿(反证一实证)。
- 只有两种情况会动到本测试:① 有人显式跑 `--refreeze`(留 git diff
  痕迹,评审可见);② schema 层 push/targets 语义变更改变了**冻结副本**
  的加载语义——这正是该测试该拦的东西(反证二实证取值漂移即红)。
- 完整性测试 `test_frozen_copies_match_golden_manifest` 钉死黄金 JSON
  键集 ↔ 冻结目录 *.yaml 文件集一一对应,防清单与目录静默失配。
