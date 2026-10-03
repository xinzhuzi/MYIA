# PRD:v1.1.2 桌面对齐批次:B 组补实现 + C 组协议/UX 缺口

## 背景

- 证据源:普查档 `.trellis/tasks/10-03-gap-census/prd.md`(本档落盘时已归档至
  `.trellis/tasks/archive/2026-10/10-03-gap-census/`;B/C/E 组缺陷矩阵与第 6 节路由,
  唯一事实源);B 组验收口径对照 `.trellis/tasks/10-02-v11-desktop-app/prd.md` 五屏范围与 Acceptance。
- 主人 2026-10-03 拍板:B2–B4(v11 桌面 PRD 未闭环项)与 C 组桌面协议/UX 缺口合并为一批(即本档),排 v1.1.1 tag 后开工。
- **路由变更回标(已随本档落盘的建档提交完成,不待开工)**:普查 Round 3 注记原把
  B2/B3/B4 补实现排 v1.2(10-03-v12-backlog 第 4 项),且 stub
  `10-03-v112-desktop-batch`(C2/C1/C3/C7 子集,planning 状态、上下文 jsonl 零内容)已先建——
  本档吸收两者全部范围。建档提交内同步完成四处回标:① stub 10-03-v112-desktop-batch
  经 task.json 标注 superseded→本档处置(选项②:新目录落稿,不复用 stub 目录);
  ② census 活清单(随普查档归档提交收口);③ 10-03-v12-backlog 第 4 项(标注已被本档
  吸收,池剩余三项不变);④ 10-03-v111-release——其第 4 项/Constraints 的「B2/B3/B4
  排 v1.2」措辞加更新注记,README 改口文案改为指向本档(v1.1.2 桌面对齐批次)或
  去版本号化「排下一批次」,赶在其写 README 改口之前,防 v1.1.1 发布时 README 写成
  过期事实。
- **划界更新(2026-10-03 ui-feature-census grill Q1 批复;晚于本草案起草/复核,落盘时按现势吸收)**:
  C8(顶栏品类选择器)与 C9(打开原文)已划归并行批 `10-03-feed-ux`(其 G2/C8 项)随批修——
  本档需求表两行保留但已标注划出,验收不再含 C8/C9;G1/G3 的 store 查询扩展与本档
  C1(游标协议)同一协议面,设计期合并考虑、PROTOCOL_VERSION 统一 +1 不各加各的。
- 在途依赖:10-03-yaml-editor(施工中)将落地 `yaml.save` 等六协议方法——C11 只剩
  settings 接线半边、C13 只剩「试抓此源」半边(增删改归彼档);C11 接线以彼档
  `yaml.save` 合入为前置。
- 基线:普查实跑记录为 pytest 1397 passed/14 skipped、vitest 40/40(时点 HEAD=45639c3);
  本档起草时未复跑,验收以开工时基线为准。当前工作区有 yaml-editor 会话未提交改动
  (dashboard 文案 + 协议测试增补),开工前先落定/rebase。

## 需求(缺陷清单;行号均为起草会话实读核正值)

| # | 级 | 缺陷 | 证据(file:line) | 建议实现口径 |
|---|---|---|---|---|
| C2 | P1 | run 无取消通道;sidecar 崩溃(Terminated)后不重启,此后所有请求永得 sidecar_not_running/terminated,顶栏「重新探测」救不回,必须重启 .app | desktop/src-tauri/src/main.rs:109-118;desktop/ui-src/src/hooks/use-sidecar-status.ts:33-54 | 壳层加 respawn(指数退避,超限转手动拉起);reprobe 升级为「探测→必要时拉起」;协议增 run.cancel 终止 run 子进程(与 C12 同源一并设计) |
| C1 | P1 | store.items 无 before/offset:同刻(相同 first_seen)条目超单页 limit 时翻页游标卡死、追加 0 条即判停 | desktop/ui-src/src/screens/feed/api.ts:7-11(自注);src/myia/store/sqlite.py:661(list_items) | 协议补游标参数(before 或 offset),feed/api.ts 判停条件改「返回数 < limit」;PROTOCOL_VERSION 随本批协议扩展统一 +1 |
| B2 | P2 | README v1.1 宣称「卡片内反馈按钮」已交付,桌面零反馈入口(CLI feedback mark/list/stats 齐全);情报流只有本地已读/星标 | src/myia/cli.py:423-449,1985;desktop/ui-src/src 全局 grep feedback 零命中 | sidecar 增 feedback.mark/list/stats(读侧直读 sqlite 已有 list_feedback:991,写侧包装 CLI 同门);feed 卡片加 👍/👎 与反馈统计入口 |
| B3 | P2 | settings 屏缺 v11 PRD 承诺的「反馈开关」分区,真实实现只有 LLM/代理池/推送三表单 | desktop/ui-src/src/screens/settings/settings-screen.tsx:62,223(共 480 行);desktop/ui-src/src/routes/settings.tsx:25-26(骨架曾列) | settings 增反馈开关分区(预算护栏);持久化走 10-03-yaml-editor 的 yaml.save 写品类节,写后 doctor 复核 |
| B4 | P2 | 仪表盘缺 v11 PRD 承诺的「采集量趋势」(只有品类卡/源健康度/近期 run 成功率;screens 下 grep trend/趋势 零命中) | desktop/ui-src/src/screens/dashboard/dashboard-screen.tsx:229-268(末卡即止) | 趋势组件自 SQLite 聚合(items 按 first_seen 逐日计数),sparkline 级轻实现;历史窗口依赖 C3 的 runs.list |
| C3 | P2 | run.status 只读内存 _RUNS,sidecar 重启即空;store 有 runs 表但无 list_runs、协议无 runs.list → 历史 run 桌面不可达 | desktop/entry.py:874-885,910-922(_HANDLERS);src/myia/store/sqlite.py:1207-1334(runs 节) | sqlite 补 list_runs(分页/新→旧),sidecar 增 runs.list 直读表;内存态仅保留「进行中」 |
| C7 | P2 | client.ts 自称「与 _HANDLERS 一一对应」失实(缺 sources.write 封装);sources/api.ts 头注释仍写「将得 method_not_found」误导(方法早已收编) | desktop/ui-src/src/lib/api/client.ts:108-135;desktop/ui-src/src/screens/sources/api.ts:8-13;desktop/entry.py:585 | client 补 sourcesWrite 封装;sources/api.ts 改走共享 client;两处头注释如实化 |
| C12 | P2 | runStart 全 UI 仅情报流空态 CTA 一处;仪表盘/日志/源管理无「跑一次」;run_busy 后无取消 | desktop/ui-src/src/screens/feed/feed-screen.tsx:250(唯一) | 顶栏或仪表盘加全局「跑一次」(选品类→run.start);取消按钮随 C2 的 run.cancel |
| C5 | P2 | secret.delete 桌面无入口,误存凭据无法从 UI 清除 | src/myia/cli.py:375;desktop/entry.py:910-922(_HANDLERS 无此方法) | sidecar 增 secret.delete(经 myia.secrets 同门);settings 凭据行加删除动作(confirm;凭据只有名字无值,不涉回显) |
| C8 | P2 | 顶栏品类选择器硬编码空壳:只有「全部品类」,C 阶段注释仍在,选中不过滤 | desktop/ui-src/src/components/layout/top-bar.tsx:25-33 | 接 health 的 plugins[].id 填充选项,选中联动情报流 store.items category;超余量则整控件移除,不得保留空壳——**已划归 10-03-feed-ux(grill Q1),随彼档验收** |
| C9 | P2 | 情报流卡片无「打开原文」外链(item.url 只进 title 悬浮提示) | desktop/ui-src/src/screens/feed/feed-screen.tsx:75-78 | 卡片加外链动作,经 Tauri shell 在系统浏览器打开(不进 webview)——**已划归 10-03-feed-ux(=其 G2),随彼档验收** |
| C10 | P2 | 协议无 app/bundle 版本字段,UI 只能拿 Python 包版本顶替(文案半边已由 02a0dce 收口,只剩本半边) | desktop/entry.py:371-373(_m_version 仅 name/version/protocol) | 壳层 spawn 时注入 app 版本 env,_m_version 透传新字段;top-bar 排障 tooltip 一并展示;与 v111-release 版本对齐后核对一致性 |
| C13 | P2 | 源管理无「试抓此源」;增删改半边已由在途 10-03-yaml-editor 收口,本档勿重复 | desktop/ui-src/src/screens/sources/sources-screen.tsx:59-84(仅启停);src/myia/cli.py:306-311,1232(myia test --source 已有) | 行内「试抓」动作包装 myia test --source(或协议等价方法),结果行内回显 |
| C11 | P2 | settings 三表单的非凭据字段(enrich.model/pools 结构/push 通道声明)不可写回,界面如实标注不伪造保存 | desktop/ui-src/src/screens/settings/api.ts:16-22(自注) | 前置=10-03-yaml-editor 的 yaml.save 合入;表单保存改走 yaml.save(品类节);pools 全局配置落点随彼档待拍板 3 定;未合入则本项顺延并回标 census |
| C4 | P2·登记 | 日志环形缓冲纯内存 4000 行,sidecar 重启即空,「回看历史日志」实际不可用(环形设计部分刻意) | desktop/entry.py:104-105,277-281 | 有余力再议:logs 落 SQLite 追加表或文件 tail;默认保持 census 登记不实现 |
| C6 | P2·登记 | pluginsList 封装全仓零调用 = 无插件管理/市场屏;倾向属 v1.2 市场 UI(无文档化计划) | desktop/ui-src/src/lib/api/client.ts:117-118 | 本批不实现;登记随 v1.2 拍板,不入本档验收 |

顺风车(可选、非门槛,implement 时按余量定):

- E4:清 desktop/ui-src/src/routes/ 五个 C 阶段占位骨架死代码(App.tsx:16-17 自述「留档备查」;B3 落地后骨架里的「反馈开关」参考位也失效),同步修正 App.tsx 头注释。
- E5:src/myia/engines/fetch_base.py:768 过期注释「留待 v02-cli-full 接线」(实际 cli.py:1079,1782 已接)改为如实描述。

## 验收标准

- [ ] C2:杀掉 sidecar 进程后,壳层自动 respawn(或顶栏重连动作拉起)恢复可用,后续请求不再永得 sidecar_not_running/terminated;冒烟演示记录入任务日志
- [ ] C2/C12:run 进行中可取消;取消后 run.status 呈可辨认终态,run 子进程不残留
- [ ] C1:用「同刻 first_seen 条目数 > 单页 limit」夹具,协议级测试证明翻页可推进直至取尽(进 tests/test_desktop_sidecar_protocol.py)
- [ ] C3:重启 .app 后仪表盘仍显示历史 run(runs.list 直读 SQLite,非内存注册表)
- [ ] B2:feed 卡片标记 👍/👎 后,CLI `myia feedback list` 能见同一条目(往返一致);反馈 stats 桌面可见
- [ ] B3:settings 出现反馈开关分区,可切换,重启 .app 后状态保留(doctor/yaml 复核)
- [ ] B4:仪表盘出现采集量趋势组件,vitest 覆盖其数据聚合
- [ ] C7:client.ts 方法集与 entry.py _HANDLERS 一一对应(含 sources.write),两处头注释无失实表述
- [ ] C5:settings 可删除凭据,删除后 secret.list 不再列出该项
- [ ] C8/C9:已划归 10-03-feed-ux(ui-feature-census grill Q1,2026-10-03)随彼批验收消号;
      本档不重复交付,仅在 C1×G1/G3 协议合参时对齐设计
- [ ] C10:version 应答含 app/bundle 版本字段,值与 .app 版本一致
- [ ] C13:源管理行「试抓此源」可执行并回显结果
- [ ] 全量 `uv run --no-sync python -m pytest -q` 与 `npm --prefix desktop/ui-src run test`、`run build` 全绿(以开工时基线为参照,新增用例另计)
- [ ] 协议新增/变更同步 tests/test_desktop_sidecar_protocol.py 与 ui-src vitest;PROTOCOL_VERSION 变更记录入 CHANGELOG(与 yaml-editor 扩展合流时统一 bump 一次)
- [ ] 可选项(C4/C6/C11、E4/E5)做或裁均在 census 档活清单(已归档:
      `.trellis/tasks/archive/2026-10/10-03-gap-census/prd.md` 文末注记)回标,裁的写明理由

## 明确不做(防范围蔓延)

- 发布工程全部归 10-03-v111-release:版本对齐 1.1.1、tag/Release/PyPI、README 升格与
  B2/B3/B4 交付宣称的「如实化改口」、updater「检查更新」接线、demo 插件——本档只交付
  实现,实现落地后的 README 宣称回填随下一次发布任务走。
- v1.2 三项归 10-03-v12-backlog:Windows 产物化、crawl4ai L3 实装、proxy_pool 对接
  (本档吸收其第 4 项 B2–B4 后,该档剩余三项不动)。
- YAML 编辑器归在途 10-03-yaml-editor:yaml.save 等六协议方法、第六屏、源增删改;
  C11 只做 settings 接线且以其合入为前置。
- 插件市场 UI(C6 主体)与全局 pools 编辑(yaml-editor 待拍板 3)不扩入本档。
- v1.1.1 已修数据通路语义(MYIA_HOME 统一/随包插件/首跑种子)不改(见 spec python/index.md「桌面发行数据根」节)。
- D3(pytest 裸跑假红)已由 718d56c 修复,不重提;普查 A 组/D 组其余条目各归 docs-truth、ci-gates 等档。

## 关联

- 普查档:`.trellis/tasks/archive/2026-10/10-03-gap-census/prd.md`(B2–B4、C1–C13、E4/E5 证据与路由)
- 口径来源:`.trellis/tasks/10-02-v11-desktop-app/prd.md`(B 组原始承诺)
- 替代与回标(四处均已随建档提交完成,无需开工再动):10-03-v112-desktop-batch
  (stub,零施工,task.json 已标注 superseded→本档)、10-03-v12-backlog 第 4 项(已注明
  被本档吸收)、census 活清单(随归档提交收口)、v111-release 改口注记(README 文案
  指向本档,防 v1.1.1 发布时写成过期事实)
- 排程前置:v1.1.1 tag 后开工(grill 2026-10-03 Q5)
- 并行批(C8/C9 划入、协议合参):`.trellis/tasks/10-03-feed-ux/prd.md`(G2/C8 项;
  C1×G1/G3 游标/查询协议统一设计,PROTOCOL_VERSION 统一 +1)

> **冲突裁定注记(2026-10-03 · 来源:零冲突收尾工作流)**
>
> 排程门 v1.1.1 tag 已过;等 types.ts / client.ts / feed-screen.tsx / settings-screen.tsx /
> sidecar-protocol.md / test_desktop_sidecar_protocol.py / App.tsx 净、且 spec 注册表演变
> (27→23 回撤)落定后,按本档 design.md §11.3 七条核对清单复核再开工;main.rs 与
> entry.py 此刻干净但属并行会话近期活跃域(entry.py 最近提交 60022ff),开工时须再核。
> (冲突证据:在途同文件(git status 实查):desktop/ui-src/src/lib/api/types.ts、
> lib/api/client.ts、screens/feed/feed-screen.tsx、screens/settings/settings-screen.tsx、
> desktop/ui-src/src/App.tsx、tests/test_desktop_sidecar_protocol.py、
> .trellis/spec/desktop/sidecar-protocol.md)
