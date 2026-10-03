# games dedup 语义修正:metric key url 优先+条目键带价格态

## Goal

grill Round 1 Q1=A(主人 /workflow 做完令按推荐)。自查实锤:pipeline.py:1689 seen 层全期拦截入库,稳定 {url} 键=条目一生只推一次——决议⑥「每日重推」实为「只推新」(勘误注记);v2 预告字段融合使正式免费日同 url 被 seen 拦截,限免 immediate 被吞(真缺陷)。修法:item_metric_key 改 url 优先(保价格基线稳定键)+games dedup 键带价格态({url}-{final_price} 或按 make_key 缺失占位实测行为定兼容形态);games 归档档勘误注记不改归档本体。

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## 实测与决议(2026-10-03 执行时落档)

### make_key 缺失占位实锤(`.venv/bin/python` 直跑)

- `DedupRegistry.make_key("{url}-{final_price}", {"url": ...})`(字段**不在** values
  映射)→ **抛 `ValueError`**「字段校验失败: dedup.key.key 模板缺少字段 'final_price'」
  (dedup.py make_key 的 missing-field 分支);pipeline `_stage_dedup` 捕获后按
  `dedup_key_error` **整条丢弃**——不是渲染空串。
- 字段在但值为 `None` → 渲染为字符串 `"None"`(键尾 `-None`,不抛错)。
- 结论:键带 `{final_price}` 的硬前提=四源每条目都携带该字段。

### 最终键形态与配套

- games `dedup.key` = **`{url}-{final_price}`**(同 url 同价=同键全期只推一次;
  变价=新键入库,免费日 immediate 落地)。
- **cheapshark 与 gog-free 都补了 `final_price`**(任务原文只点名 cheapshark,
  但 GOG 条目同样缺该字段——不补则 GOG 源在 `_stage_dedup` 全军覆没,属任务
  列举不全的对称补齐):CS `"$[*].salePrice"`、GOG
  `"$.products[*].price.finalMoney.amount"`,均为**美元元字符串**,三条安全性
  论证写进 games.yaml 注释——①模板 elif 链已重排 sale_price 先于 final_price,
  美元条目走 $ 段不触达 `final_price / 100`(对 str 会 TypeError,重排前实测复现
  炸卡);②限免规则 `final_price == 0` 对字符串恒 False 不误判(GOG 限免判定
  继续走 sale_price/normal_price 析取,CS 实测无 0 元 deal);③`numeric_value`
  拒 str,美元字符串不写人民币分基线。已知代价:digest 路由 when
  `final_price > 0` 对字符串 TypeError→按不命中→保守缺省 digest(落点与改前
  一致,仅逐条 WARNING,与大折扣规则对 CS 条目的既有噪音同类)。
- `item_metric_key` 反转为 **url 优先,退 dedup_key**(templates.py):条目键带
  状态后,价格基线的稳定身份=商品 url;games 键随价格态轮换、stocks 键随
  槽位轮换(`{symbol}-{date}-{slot}`),旧序下每日快照落不同键、vs_yesterday
  恒空,反转后按 url 取昨值不断链。gpu-prices({url} 键)不受影响。
- golden 基件 `tests/fixtures/push_targets_golden_before.json` games 条目同步
  再生成(dedup.key/两模板/CS+GOG final_price 四处,循 v3/wrap 同法:当前
  YAML model_dump 剥 push 侧空缺省键)。

### 勘误注记(不改 games 归档档本体)

- **决议⑥勘误**(10-03-games grill 决议⑥「长促销会在每个新槽位/新日重推一次
  (窗口期可见性优先)」):实际语义=**只推新条目**。槽位抑制(should_send/
  同槽拦截)只作用于推送层;入库层的 `is_seen` 是全期检查(dedup.py
  `is_seen`:条目见过即永拒),稳定 `{url}` 键下条目一生只推一次,不存在
  「每日重推」。本修复以键带价格态部分恢复重推语义:价格变化(含正式免费日)
  才是再推信号。games.yaml 排程注释已同步改写。
- **v2 决议②勘误**(10-03-games-v2 决议②「预告日 digest、正式限免日新槽
  immediate,切换碰撞由槽位抑制语义自然消解」):消解判断错误——同 url 在
  预告日入 seen 后,免费日条目被全期拦截,immediate 被吞(本任务靶心缺陷)。
  修后语义:**免费日 final_price 变 0 = 新键 `{url}-0` 入库,immediate 照常
  落地**;预告日条目键为 `{url}-5300`,两态互不干扰。games.yaml classify
  注释与 test_plugins.py 对应用例 docstring 已按此改写。

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
