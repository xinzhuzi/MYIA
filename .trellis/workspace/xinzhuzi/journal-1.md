# Journal - xinzhuzi (Part 1)

> AI development session journal
> Started: 2026-10-01

---

## 2026-10-02 — 10-02-v11-plugins-source-arch 验收实跑落档

- AC#1 myia-osint 零 docker 真实侦察:`.venv/bin/python -m myia.cli osint https://example.com --json --timeout 240` → 退出码 0,status=success,vendor.commit 与 submodule pin 一致,子进程 Photon 侦察 3.4s 完成并产出结构化端点(internal/external/endpoints)。**验证通过 2026-10-02**
- AC#2 myia-proxy 轻量路径(进程内,零 Redis 零 docker):`.venv/bin/python -m myia.cli proxy --count 3 --timeout 8 --json` → 退出码 0,mode=in_process,status=success,3 个公开源全部抓取成功(约 2360 候选),测活 13 个、3 个可用(含延迟)。**验证通过 2026-10-02**

## 2026-10-03 v1.1.1 桌面数据通路修复(task 10-03-v111-desktop-paths)

- 按工单按图施工完成:entry.py `_serve_context()` 四级优先级(params>MYIA_HOME>.app 探测>dev cwd)+ 首跑种子(.seeded 幂等)+ health `first_run`;main.rs 注入 MYIA_HOME;tauri.conf resources 带官方四件套;feed 空态「运行第一个插件」CTA
- 施工中新发现并修掉:全新数据根 `<home>/plugins` 不存在时 health 仍报 plugins_dir(探查矩阵「无创建逻辑」的残余)→ 解析即建目录
- 验收实测:cwd=/ 全方法矩阵 OK(两条路都验:bundle 探测 + 真首跑 Rust 注入);种子四件套+db 落数据根;pytest 31+vitest 40 绿
- 口径修正(design.md D7):「plugins.list ≥4」不可达也不应达成 —— 官方插件是品类 YAML,plugins.list 是市场面;验收改为 health.plugins ≥4
- 遗留(非本任务):tests/test_baseline.py 一例预存失败(`ModuleNotFoundError: No module named 'tests'`,干净 main 复现);四件套 push 全引用 env:FEISHU_CHAT_ID,无凭据首跑=引导态(设计);截图像素级核对留主人
- Ruling: seed 判「目录无 *.yaml/*.yml」而非整目录空 —— 市场插件子目录不算品类,避免装了市场插件就拒绝种子官方件

## 2026-10-03 grill Round 1(主人×AI,决议全采纳,档=10-03-grill-v112)

- Q1 下一刀=发布工程先行(tag v1.1.1);Windows+crawl4ai L3+proxy_pool 排 v1.2(池档 10-03-v12-backlog)
- Q2 版本号对齐叙事:四处 0.1.0→1.1.1,tag v1.1.1,README 徽章 alpha→1.1;否决 0.2.0(与 v1.0 已交付叙事矛盾)
- Q3 开箱即有数据:随包第五件 myia-demo.yaml(真实免费源+stdout,零凭据),兼作 README 截图素材
- Q4 挂账处置:v10-release review→in_progress 重开(未勾验收移交 10-03-v111-release);crawl4ai/proxy-transport 注记 v0.2 已毕不重开
- Q5 test_baseline 预存失败:授权当场修(单独 commit)
- Q6 决议+执行项全部当轮落 trellis(3 子任务挂 grill-v112)
- Round 2 待主人批:R2-1 updater 三密钥生成+Secrets / R2-2 Apple 公证($99/年)买不买 / R2-3 PyPI 先 test.pypi 演练 / R2-4 四帖节奏与定稿人
- 事实修正:CI 非零起步(desktop-release.yml windows job 已在、pypi-publish.yml 手动 dispatch 已在);「发布零起步」旧口径作废

## 2026-10-03 桌面端 YAML 配置编辑器立项(task 10-03-yaml-editor,planning)

- 主人指示必须有 YAML 编辑模块;当轮探查落档(prd/design/implement 三件套齐,待拍板 5 项后 start)
- 探查要点:品类 YAML 是核心配置面,桌面五屏无查看/编辑入口;sidecar `_HANDLERS` 11 方法扩展点干净(sources.write 先例);校验同门 load_category、原子写先例 `_atomic_write_text` 均可复用
- 形态决议(推荐待批):独立第六屏「配置编辑」+ 源管理行「编辑」跳转;CodeMirror 6 做编辑器;myia-* 插件系统是数据源场景、无 UI 插件机制,编辑器以 UI 模块 + 协议扩展落地
- 顺带挖出地雷:sources.write 写回用 yaml.safe_dump(entry.py:687),**启停开关即抹掉品类 YAML 全部注释**(官方件大半是注释)——是否并入本任务修,列待拍板 1
- 关联普查档:C11(yaml.write 泛化缺失)由 yaml.save 收口;C13 增删改由原文编辑覆盖
- 追问补范围(同日):「用户自建品类」原 v1 范围真空 → 补 新建(yaml.template + save 的 null-mtime 语义)/删除(yaml.delete 连带清 .disabled.json)/跨文件重复品类 id 拒绝(现状 loader 零守卫,手拷改源忘改 id 会静默混品类——写盘门收口);方法 4→6,C13 增删改全链路收口
- grill Round 1(同日,主人全按推荐批复并令落实):排期=文档定稿、start 等 release 收尾信号;启停抹注释=止血(.bak)并入本任务+根治拆 10-03-yaml-toggle-comments(已建);.bak 单份滚动;pools config 不纳入;CM6 依赖实锤(npm 实测 peer react>=17,React 19 兼容);单最小模板;保存闭环(自动 doctor 复核+「跑一次」dirty 禁用);keychain 对照 secret.list 出 warning 不拒写(env 不对照);官方件删除只写 confirm 文案(恢复按钮 backlog);dev 模式允许改仓库件+路径可见。三件套已按决议全部更新,任务达「可 start」状态



- 主人点名两处不需要:侧栏「v1.1 骨架 · 业务接入见各屏空态」、顶栏「sidecar v0.1.0 · 协议 v1」→ 侧栏 footer 整块删;顶栏在线态只留绿点,版本/协议退 title 悬浮(排障不丢);connecting/error 态未被点名不动
- 验证:vitest 40/40 + tsc/vite build 绿(显式退出码);构建产物 grep 两处文案零残留、tooltip 模板仍在;tauri build 重装 /Applications(旧版备份 /tmp/MYIA.app.bak-hints-trim 可删);OCR 截屏目视:侧栏无骨架行、顶栏无版本字样,羊毛/显卡情报正常渲染
- 冒烟两个坑:①macOS 文件系统大小写不敏感,直跑 MacOS/MYIA 实际执行的是 sidecar(打 argparse help),看壳要看进程 myia-desktop;②System Events 窗口计数对后台窗口报 0,先 activate 再数
- 普查档 C10 文案半边由此收口(版本字段半边留档);spec 无涉(纯 UI 文案);单 commit

## 2026-10-03 grill Round 2/3(主人全按推荐;决议全文=10-03-grill-v112)

- Round 2 批复:R2-1 updater 做全(三密钥+UI「检查更新」接线入 v111-release,密钥=主人侧前置);R2-2 不买 Apple Developer,README 写右键打开指引;R2-3 PyPI 先 test.pypi 演练;R2-4 tag+PyPI 后发四帖(AI 素材/主人定稿)
- Round 3(trellis 文档补全):Q5 普查路由全批+排序修正(立即=推提交+docs 小修;tag 后=ci-gates/v1.1.2 批次/归档会话);Q6 B2/B3/B4=v1.1.1 改宣称、补实现排 v1.2(覆盖普查路由②的回炉);Q7 归档会话授权;Q8 task.py finish 跨会话防护(搭 docs-truth)
- 执行档全家福(全挂 grill-v112):v111-release(P0)/docs-truth(P1)/v112-desktop-batch(P1)/ci-gates/archive-review/v12-backlog(+B2/B3/B4)/test-baseline-import(已交付 review)
- 普查档转活清单(D3 ✅718d56c、B1 ✅定向、C10 ✅02a0dce、B5 ✅获批即推),状态归 review
- 并行会话协调:ui-hints-trim 已交付归档(02a0dce/49e223b),v111-release 的 C10 引用已修注

## 2026-10-03 侧栏品牌头移除(task 10-03-sidebar-brand-trim,review)

- 主人截屏(448×110)点名侧栏品牌头不需要 → 整块删;MyiaWordmark 零引用连带删,MyiaMark 留(empty-state 在用);index.html meta description 同文案属 Tauri 死上下文一并删行;nav 顶部补 pt-4 防贴顶
- 验证:vitest 40/40 + tsc/vite build 绿(显式退出码);源码+产物 grep「替主人看着世界/MyiaWordmark」零残留;tauri build 重装 /Applications(旧版备份 /tmp/MYIA.app.bak-brand-trim 可删);窗口截屏 OCR 目视:品牌头无、五项导航在序、仪表盘数据正常,孤例「MYIA」为系统标题栏窗口标题非字标残留
- 冒烟新坑(接 ui-hints-trim 两条):③System Events 窗口计数 0 ≠ 无窗口——CGWindowList(.optionAll) 仍可见,`screencapture -l <winID>` 可直截非当前 Space 窗口;④sips --cropToHeightWidth 是居中裁剪且 --cropOffset 无效,要区域图直接 screencapture -R 指定坐标
- spec 无涉(纯 UI 布局裁剪);单 commit(仅本任务三文件+任务目录,journal 压着 yaml-editor 两行未随行)

## 2026-10-03 UI 功能对标普查(task 10-03-ui-feature-census,planning 待拍板)

- 主人指示查业界情报/爬虫软件找 UI 缺口 → 三路网调(OSINT 5 件/爬虫平台 6 件/订阅监听 6 件,能力点全带官方 URL,原文留任务 research/)+ MYIA 五屏+顶栏源码实读基线(commit d84ec05 时点)
- 缺口矩阵 G1-G12:P1=条目搜索/详情与打开原文/数据导出/排程管理+手动触发/推送测试按钮;P2=趋势折线/run 重跑过滤/日志搜索/AI 摘要按钮/告警规则(Rules 式)/就地沉淀关键词(OpenCTI 铃铛式亮点);P3=快捷键批量/代理连通测试/凭据整包迁移
- F 类延伸四条刻意不做:无代码点选构建器(AI-native 定调冲突)/案件图谱协作域/OPML 模板市场/云端索引类能力
- 与已有档划界不重复:源编辑→yaml-editor、看图→image-input、检查更新→v111-release、feed 游标分页→gap-census C 类;G1-G4 建议并为 feed-ux 批次、G5 告警规则是否入 v1.2 等 4 项待主人拍板
- 仅普查未动代码;顶栏品类 Select 死骨架(defaultValue="all" 未接数据)顺带记入基线事实

## 2026-10-03 trellis 任务档补全(9 档 jsonl 清单,commit 8e8337d)

- 主人指示补全任务文档 → 69 个非归档档全量审计:PRD/描述/优先级全齐(工作流写的 messaging 四子档连 design+implement 都有),唯一系统缺口 = 9 档 implement/check.jsonl 还是空占位(grill-v112 决议族:七个执行档+普查档+test-baseline)
- 18 份清单按各档真实材料源策填:普查证据矩阵、决议档、spec python 红线、yaml-editor design 契约(给 yaml-toggle-comments)、ui-feature-census G 矩阵(给 v112-desktop-batch 防重复立项);task.py validate 全绿、引用存在性零悬空
- 填写中途并行归档会话把 gap-census 收档(f8664a6,顺带搬走刚填好的 gap-census jsonl),9 处引用即时改锚 archive/2026-10/;v111-release 的 jsonl/task.json 同期也在被并行会话改,未触碰
- 工程注记:并发会话共享工作区时,填档/引用类操作提交前必须重跑悬空引用检查(这次就真撞上了)

## 2026-10-03 ui-feature-census grill Round 1(主人四问全按推荐;feed-ux 立项)

- grilling 规程一轮收口设计树:Q1 feed-ux 批次立项(10-03-feed-ux,G1 搜索+G2 详情原文含 C9+G3 导出+G4 排程管理/C8 接线/G5 测试按钮搭车;C5/C13 留 v112-batch;G1/G3 与 C1 协议合参、版本统一 +1;tag 后并行;批内 G2→G4→G1/G3)
- Q2 P2/P3 全部入 v12-backlog 池不加码(v1.2 承诺仍=Windows+L3+proxy_pool+B2/B3/B4);Q3 F 类五条全认可(新增 G11 凭据导出不做,secret set 重录);Q4 v1.2 前置都有(Windows 真机、proxy 服务商预算)——已回写 v12-backlog
- feed-ux 建档含设计期事实:桌面仅 shell 插件、capabilities 无 fs/dialog/opener JS 权限——G2 打开原文与 G3 导出的通道选型是 design.md 必答项
- 回写四处:ui-feature-census 拍板注记+状态转 review;v12-backlog 增 G 项入池节+前置已答节;v112-desktop-batch 划界注记;新档 feed-ux(prd+jsonl 齐全,validate 绿)
- 主人令「按建议落实」→ feed-ux 补齐 design.md+implement.md 达可 start:事实核订五项(capabilities 安全姿势=webview 零 shell 执行授权、dialog 前后端已装、store.items 已有 category 参、sidecar 不驻留调度器→排程管理=可视化+预览+手动触发、PROTOCOL_VERSION=1 与在途 yaml.* 线合并时统一 bump 不抢跑);协议四件=store.items 增 query/before(与 C1 合参)、feed.export(dialog.save 选路径 sidecar 直写)、push.test(真发,stdout 默认)、schedule.preview(build_cron_trigger 纯算);UI 六步+回滚点+守门(shell:allow-open 必须 scope https)

## 2026-10-03 普查路由落档:三修复任务定稿 + 普查档归档(落档会话)

- 依主人批准的普查路由(grill Round 3/Q5)落 3 个修复任务,草案经复核修订后定稿:`.trellis/tasks/10-03-docs-truth/`(A 组 docs 侧 pip 宣称 + env.example 键名 A3/A4 + task.py finish 跨会话防护 Q8;正式稿取代 07:50 占位稿)、`.trellis/tasks/10-03-v112-desktop-parity/`(B2–B4 补实现 + C 组全量,吸收 stub 10-03-v112-desktop-batch 与 v12-backlog 第 4 项,新目录落稿)、`.trellis/tasks/10-03-ci-gates/`(D1 rust-check + D2 ruff 最小门禁;取代占位稿);三档 context 双侧(implement/check)齐挂,validate 全绿
- 普查档 10-03-gap-census 验收第 4 条补注记(主人批准路由,3 任务已建)+ 活清单回标收口后归档至 `.trellis/tasks/archive/2026-10/`(commit f8664a6);归档顺修普查档 D4 行号 92-96→99-104
- 复核意见四条全吸收:①②回标不待开工——stub task.json 标 superseded→v112-desktop-parity、v12-backlog 第 4 项移交注记、v111-release 改口注记(README 文案改指 v1.1.2 批次,防 v1.1.1 发布时写成过期事实)均随建档 commit 完成;③ci-gates D4 证据改 build-sidecar.sh:99-104(pyinstaller `--specpath`,SPIKE_DIR 定义在 15 行,顺修草案 --specpass 笔误);④D3 验收改浮动基线口径(普查时点 1397/14,期间 v111-release/yaml-editor 移动总数,零失败为准)
- 落盘按现势增补(晚于草案的新拍板):C8/C9 已按 ui-feature-census grill Q1 划归 10-03-feed-ux——v112 正式档表格标注划出、验收不含,并记 C1×G1/G3 协议合参(PROTOCOL_VERSION 统一 +1);v112-desktop-parity 挂 grill-v112 子任务与执行树对齐
- journal 仍压着 yaml-editor 两行与 sidebar-brand-trim/ui-feature-census 两节未随行(其他会话内容,照旧不代提交);desktop/tests/.trellis/scripts 在途改动(Q8 防护施工中)一律未碰

## 2026-10-03 CI 门禁补全(task 10-03-ci-gates,implemented→归档;提交人落库)

- 基线(PRD 起草期实测,本机+/tmp 干净克隆):fresh clone 裸 `cargo check` 必红——tauri-build build.rs 校验 externalBin 而 binaries/ 被 gitignore(.gitignore:67),造空占位后全绿;ruff 0.16.10 默认集 498 错(I001×68/UP017×60/F401×47),最小集 E9/F63/F7/F82 0 错零改动即可绿
- 门禁落地:ci.yml 增两 job——`rust-check`(占位 externalBin → `cargo check --locked`,working-directory desktop/src-tauri)与 `ruff`(`uvx ruff@0.16.10 check .` 锁版本);D3 防回归注记进 tests/conftest.py docstring(测试一律 `from conftest import`,勿改回 `from tests.conftest import`——裸 pytest 假红,修复史 718d56c);D5 取 PRD 两选项中的文档提示路线:desktop/UPDATER.md 补「`tauri dev` 前先 bash build-sidecar.sh」本地开发提示,不动 tauri.conf.json
- 偏离四条:①D4 删 myia.spec 跳过——git status 显 M,v111-desktop-paths 会话在途脏改且它正是重新生成该对象的会话,碰撞规避留其收尾;②ruff 配置落独立 ruff.toml 而非 PRD 的 pyproject [tool.ruff]+dev 组加 ruff——pyproject 被 v111 版本对齐在途改,缘由注明在 ruff.toml 文件头,CI/本地同款命令不变;③验收「push 上 ci.yml 真跑全绿」不在本批,多会话在途此刻推送会夹带未完成改动,归编排脚本合并后补绿 run;④普查档活清单回标 D1/D2/D4/D5 不在本批(本 journal 节即其中「记一笔」)
- 提交人复核探针(2026-10-03,本机):`uvx ruff@0.16.10 check .` 全工作树 1 错——F821 于 tests/test_messaging_pipeline.py:169,该文件 ?? 未跟踪(他会话在途件,干净 CI 检出不存在,不拦门禁);对 HEAD 跟踪树(`git ls-files '*.py'` 130 文件同款命令)All checks passed exit 0。cargo check 未在本会话复跑(依赖本机 binaries/ 占位与网络拉依赖,PRD 起草期已双态实测,CI 首跑以真实日志为准)
- 落库:单 commit A 仅白名单五路径(ci.yml/ruff.toml/conftest.py/UPDATER.md/任务目录)+ journal 本节选择性暂存(git apply --cached,文件其余在途内容不代提交);归档 task.py archive --skip-branch-validation 自动提交

## 2026-10-03 YAML 配置编辑器落地(task 10-03-yaml-editor,implemented→待人工冒烟)

- 主人令「做完它」推翻决议 1 排期,动态工作流(dwfrun-202c9176,七阶段六代理)施工:协议六方法(yaml.list/read/validate/template/save/delete:围栏+stem 正则+findings 分级+secret_unknown warning+新建 null-mtime 语义+跨文件 id 查重+delete 连带清暂存+两侧 newline='' 字节保真)+ sources.write 止血(.bak)+ 配置编辑屏(CodeMirror,useBlocker 数据路由守卫,Cmd+S/跑一次/自动 doctor 复核/空目录 CTA)+ 源管理「编辑」联动 + 协议 spec 落档(.trellis/spec/desktop/sidecar-protocol.md,23 方法注册表与错误码,C7 防腐)
- 门全绿:协议契约 pytest、全量 1620 passed/14 skipped、vitest 86、tsc/vite build;契约独立核对两条 medium(useBlocker 假路由、.bak 内容无专测)运行内修复闭环;余 low 主会话收口:幻影依赖 @codemirror/theme-one-dark 显式声明、四缺口用例独立成档 tests/test_yaml_editor_protocol_gaps.py(明文凭据双门/超长 stem/save 侧 1MiB;主协议测试文件是会话热点,他会在途 +17 行不混提交;跨文件导入循 conftest 裸模块新规)
- 并行会话宽 add 把主体实现卷入 b4e2787(messaging/CI 批次),代码无损、归属混;本会话提交仅收尾件
- 待人工:GUI 冒烟(tauri 窗口真文件往返/注释逐字节/新建后源管理即时可见);task 留 in_progress,冒烟过再 finish-work;根治任务 10-03-yaml-toggle-comments 独立未动

## 2026-10-03 游戏情报品类插件立项(task 10-03-games,backlog)

- 主人令「相关的游戏情报也要做」→ 全库探查:游戏零覆盖;情报覆盖=七大类+channel(builtin 表,忠实移植 wf_crawl.py 红线不扩)之外,stocks/gpu-prices 已立"七类外情报走品类插件"先例 → 游戏情报同路线 `plugins/games.yaml`
- 当轮建任务(--no-start,大工作流 dwfrun-9808b474 在途且其 12 任务清单固定不含本档,不抢指针);PRD 落:来源候选(Epic 限免 JSON/Steam price_overview/Reddit .json/中文 L2,设计期核实)、classify.builtin=false+限免/折扣力度 rules、dedup 禁裸 {title}、限免 immediate/折扣 digest、史低 baseline 可选项;验收挂 test_plugins.py 参数化基线(新 yaml 自动全套)+ CI 零外网录制回放
- 开工前主人四问:平台范围/要不要资讯/中英文源偏好/史低基线进不进首版
- **深化(同日,主人令「深化与完善trellis任务文档」,commit 5bd149a,三件套齐达可 start)**:实跑探源定稿——Epic freeGamesPromotions ✅(zh-CN,12 元素,robots 404 无限制)、Steam featuredcategories specials ✅(discount_percent 直出,robots 不禁 /api/)、Reddit .json ❌(honest UA 回 HTML,剔除)、中文 L2 未探留 backlog;核订关键事实:①两源响应均无页面 URL(只有 slug/appid)= stocks 记录过的 schema 缺口 → D1 本任务内做 extract.url_template 微扩展(可选字段+二选一校验,向后兼容);②规则求值缺字段读 None 且含 None 运算整条跳过 → 两源同名归一化字段(final_price/discount_pct,两家单位同为分);③dedup 抑制是槽位作用域且 baseline metric key=dedup_key 优先 → dedup.key 必须稳定 {url}(带 {date} 断价格历史);④OFFICIAL_PLUGINS 是显式元组非 glob(PRD 初稿「自动全套」口径已修正;gpu-prices 不在元组=顺带观察);⑤watchlist 在 enrich 关闭时零降权;探源响应存任务 evidence/ 作 fixture 素材;PRD 四问全带推荐(Epic+Steam 起步/只做折扣限免/英文 API 先行/price 基线进 v1 msrp 不进);validate 绿,任务仍 backlog 未 start
- **grill Round 1 收口(同日,grilling 技能一轮清前沿 7 问,主人「按照你的建议去做」=全按推荐,commit b0add6a)**:①平台=Epic+Steam 起步(GOG/Switch 拆后续先探查)②内容=v1 只做折扣/限免,upcoming 预告=v2 首项,资讯 backlog③源=英文官方 API 先行④基线=price 进 v1、msrp 不进⑤通道=feishu_card 单通道(tg 后续加挂)⑥限免每日 immediate 重推=接受(调稀改 cron)⑦D1 url_template 微扩展已批(D2 占位否决)⑧gpu-prices 补测试元组=独立待办⑨开工触发=大工作流 dwfrun-9808b474 收尾后主人下令(schema.py/test_plugins.py 热点路径防同树双线)。九条决议回写 prd「Grill 决议」节/design D1·D5·§5/implement 前置注记/task.json notes;决议编号统一 ①-⑨ 口径;标题去「资讯」对齐 v1 范围;validate 绿,无悬而未决项,任务达「可 start 等开工令」终态
- **执行完毕(同日,主人经 /workflow 下令,动态工作流三跑收口,commit c227d57,任务转 review,未 push)**:落地 16 文件 650+ 行——extract.url_template 扩展(schema 校验三件:至少一占位/type=item 拒/占位-字段交叉 fail-fast)+fetch_base 提取出口渲染(复用 dedup 迷你模板,int 渲染成 str)+plugins/games.yaml(12 段+baseline,Epic/Steam 双源)+OFFICIAL_PLUGINS 加名+_SNIPPETS 双源录制 fixture+测试净增 66 条(1674→1740)+spec/六处文档锁定面同步+live-run 证据;门禁全绿(本任务范围零失败、ruff 零错、dry-run 0、真跑 Epic 12/Steam 10/immediate 2=本周真有免费游戏);独立质检 7 发现:2 medium 修(url_template 占位交叉校验进 _check_shape、空 url 真实语义=invalid_item 拒条目而非常规「链接差」——design R1 与文档锁定面全部改述)、1 medium 驳(stocks 红非本任务)、4 low 留档(hash-slug 可点性未验/expire 字段未提取/fixture 第二形状无断言/分转元无值级断言)。**工作流三跑教训**:①基线零红门禁在多会话工作树必假死——首跑即被 yaml-editor 线在途红(test_sources_write_backup)拦停,改为「外来红记录放行+终跑不得新增」口径(ci-gates 先例);②实现者上报 stocks.yaml schedule 0→30 无人认领漂移(mtime 11:07,全历史/任务档零出处,6 钉位皆 0)拖红 2 用例——按主人铁律未还原未提交改动,门禁精确豁免两节点,**待主人认领(连钉位一起改)或还原**;③AmendWorkflow 修门禁语义零成本(未决 ask 恰在缓存边界外,实现阶段全免重付)
- **4 low 收尾(同日,主人批「按照你的建议去做」,主会话直做(yaml-editor 先例),commit 24b88b3,任务保持 review)**:①hash-slug **结构性消解**——实测 12/12 元素 catalogNs.mappings[0].pageSlug 全覆盖且即官方 slug 形态(urlSlug 4/12 是 32 位 hash),url_slug 提取路径换到 mappings,真跑复核 0 hash URL(Model Builder/Eternal Threads 等出正规链);speculative 升格为实证;②expire 字段入库($.specials.items[*].discount_expiration,供后续 UI/排序;沙箱无日期格式化,模板不显示——design 注记同步);③_SNIPPETS 第二差异形状断言(expect_second opt-in 机制:Epic=TerraScape hash+无促销形状钉 pageSlug 渲染与 discount_pct 逐元素省略,Steam=How to Fish 钉不错位+expire);④分转元值级钉住(模板测试断言 13.6=1360/100)。门禁:scoped 67 passed、全量 1759 passed **零失败**、ruff py 零错、dry-run 0、真跑 exit=3 系 shell 无凭据引导态(设计)非代码问题;证据 evidence/live-run-lowfix-2026-10-03.json。**stocks.yaml schedule 漂移已认领闭案**(=yaml-editor 无头冒烟的 AC2 编辑,mtime/内容全吻合;11:39 被冒烟清场 git checkout 还原,两外来红随转绿——我会话全程未动该文件,主人无需再拍板)

## 2026-10-03 免费层供应商地图落档(task 10-03-free-tier-supplier-map,research→review)

- 起因:主人问 free-for.dev 对本项目是否有帮助;首答「无工程增量」被判框架错(拿省钱账本量情报)——改用决策框架重评:它是 MYIA 的供应商采购目录+趋势信号+反情报,当场立项蒸馏
- 交付 research.md:四决策面(push 通道/engines 抓取/vision+enrich 端点/项目运营)26 条候选,五要素齐(免费层快照/信号/自托管替代/适配度/核实状态),●=16 条本轮经 zread 核实(快照 2026-10-03),○=10 条写入用户文档前须二次核实;中文生态盲区显式补位(Bark/Server酱/GLM-4V-Flash/SiliconFlow,free-for.dev 西方中心不覆盖)
- 反情报要点落档:Fly.io/X API 免费层翻车案例、free tier trap(结构性营销)、「云端连接器必须有免费路径」选型硬规则建议、OpenAI 兼容 base_url 是 MYIA 天然接口(vision/enrich 换 base_url 即接 OpenRouter :free 等)
- 网络注意:本机 gh api/raw.githubusercontent 直连不通(gh search/zread 服务端通道正常,事实均双通道交叉核实);free-for.dev 无 LICENSE→只记要点与链接不搬运原文
- 状态直改 review(并行工作流 dwfrun-9808b474 在途,循例不用 start/finish 抢指针);后续出口三条在 research.md 末尾(用户零成本接入指引/选型 spec 硬规则/push 扩展候选池)

## 2026-10-03 supplier-map grill 补全决议落地(五问全按推荐)

- grill(grilling 技能)对 10-03-free-tier-supplier-map 文档拷问一轮清前沿,主人令「按推荐去做」:Q1 出口一立 `10-03-zero-cost-setup`(docs/zh|en/zero-cost.md,zh 先 en 后,PRD 硬约束=落笔前核 ○ 升 ●,planning 不启动);Q2 出口二落 `.trellis/spec/domain/connector-selection.md`(云端连接器必须有免费路径+信号分级,根 spec/index.md 已挂行);Q3 push 候选入池不立项排期权留主人;Q4 ○ 项「引用时核+回写升 ●」机制录 research.md 末节;Q5 活档案,zero-cost-setup 执行完成后 archive(trigger 已记 task.json notes)
- spec 撰写循 trellis-update-spec 技能(设计决策型,非 7 段基建契约);domain 层格式对齐 security-baseline(铁律短句+grill 引注);仓库公开红线照办(全部文档无凭据无私有痕迹)

## 2026-10-03 桌面端静默启动(task 10-03-quiet-launch,in_progress→review)

- 起因:主人令「打开 MYIA 要静默,不要每次都切到 MYIA」。全量探查定位双根因:tao-0.37.1 启动回调无条件 activateIgnoringOtherApps(true)(app_state.rs:293,默认值 app_delegate.rs:106,Tauri 2.12 未暴露关闭口)+ 窗口默认可见即 makeKeyAndOrderFront;基线实测 open -g 后台启动照样抢前台
- 修法:主窗口 visible/focus:false 出厂;RunEvent::Reopen(Dock 点/运行中再 open -a)才 show+focus;静默启动后 500ms NSApp.deactivate() 让回前一应用(objc2-app-kit 0.3.2 锁内同版);dev 构建与 MYIA_SHOW_ON_START=1 例外照常显示(open 不透传 shell env,发布包验证直跑二进制或 open 两次)
- 验收(纯文字转录,无截图):open -g 冷启动 frontmost 保持 ZCode、窗口 0、myia-core serve 在跑;运行中再 open -a 亮窗置前;env 直跑窗口 1、sidecar 212ms;quit 干净无残留;cargo check+tauri build 绿(app 124.98MiB,旧包留底 /tmp/MYIA.app.bak-quiet-launch)。Dock 点击与 tauri dev 亮窗留主人自验(同 Reopen 路径/cfg!(debug_assertions) 恒真)
- 踩坑三记:①git 相对路径 pathspec 随 shell cwd 漂移(cwd 停在 src-tauri 时 diff 全空,险误判 stash 竞态)②main.rs/tauri.conf.json 混着并行工作流未提交的 sidecar 改名 hunks——选择性 git apply --cached 只提交自己的 hunks,别整文件 add ③PyInstaller bincache 并发写坏(重试即过);另:os-etiquette 铁律 4 落档后全屏截图禁留证,两张实测截图已删改文字转录
- 已知取舍:updater relaunch 后同样静默;发布包 UI 验证流须换姿势(直跑二进制带 env 或 open 两次)——v112-desktop-parity 冒烟步骤要用新姿势;状态直改 review(循例不动指针)

## 2026-10-03 单实例锁 + 现场清理(task 10-03-single-instance,in_progress→review)

- 起因:主人令「这个项目只能保持 1 个实例,无用的内容清理一下」(承接 myia-desktop dev 实例与装机包并存的截图报障)
- 清理:退 dev 实例(99135)+dev sidecar、5173 陈旧 vite+esbuild、/tmp/myia-repro 孤儿 server.py:8731(1d10h 零引用);留 5199 vite(yaml-editor 现役)与冒烟 sidecar 对(bridge.mjs 活体在守)
- 实现:数据根(MYIA_HOME 同规则)下 .instance.lock,flock 独占,进程退出内核放锁;拿不到锁→孤儿 shell sleep 0.5 后 open -b com.myia.app(既有实例走 Reopen 亮窗)再退出。官方 single-instance 插件 macOS 是空操作,自持锁是唯一覆盖 open -n/直跑/dev+release 混跑的路。libc 入 macOS 依赖(锁内同版)
- 踩坑:第二实例「当场」open -b 有竞态——两进程短暂同 bundle id,LaunchServices 激活将死者,实测窗口不亮;改孤儿 shell 延时激活后复测过(open -n 后仍 1 进程+亮窗置前)
- 验收:open -g 静默 1 进程/open -n 秒退+亮窗/直跑二进制秒退/quit 放锁重启正常/cargo check+build 绿重装;终态恰好 1 个实例静默在跑
- 已知取舍:MYIA_HOME 沙箱=独立实例域(验证隔离是特性);tauri dev kill→spawn 毫秒级放锁窗口竞态≈0,工作流偶发秒退重试即过

## 2026-10-03 零成本接入指引按 trellis 执行(task 10-03-zero-cost-setup,workflow)
- 核实:6 家免费层政策(GLM/SiliconFlow/Groq/Mistral+OpenRouter),全部可核
- 中文版:质检通过;英文版:已产出;终检:通过;密钥样式扫描:0 命中
- 状态:review(直改 task.json,并行工作流在场不抢指针);supplier-map 归档触发条件达成

## 2026-10-03 定名「世事」+ 眼后宇宙图标(task 10-03-rename-shishi,in_progress→review)

- 主人定案:产品名 MYIA→世事;图标=眼睛背后是一个宇宙。范围=桌面显示面(productName/窗口标题/html title/UI 标 alt)+ branding 唯一事实源重绘 + 发布产物名;内部标识符(com.myia.app/MYIA_HOME/myia-core/mainBinaryName=MYIA)刻意不动——数据根/单实例锁域/updater 身份零迁移;repo/PyPI/CLI 名留主人拍板
- 图标:Python 实算生成 SVG(对数旋臂 a=112/b=0.30/倾角-0.32,种子 42;零滤镜纯形状守 branding 规矩)→ rsvg 1024 → npx tauri icon 全尺寸。眼形轮廓/青→紫渐变/信号触点延续品牌;眼底透明化透出星系,瞳孔改黑洞芯+光子环+吸积弧+瞳中星。像素采样自检 7 点过(圆角透明/黑洞暗/核辉亮/臂带色/眼描边亮/触点紫/深空底);美学目验留主人
- 验证:PlistBuddy CFBundleDisplayName/CFBundleName=世事、窗口名=世事、vitest 89/89、build 绿(世事.app 125.07MiB);装机换名(旧 MYIA.app 备份 /tmp/MYIA.app.bak-pre-shishi);open -g 静默、quit app 世事 正常;顺带清掉一个从 target/bundle 拉起的 stale MYIA 实例(曾持锁导致 open -b 激活错对象)
- desktop-release.yml/UPDATER.md 产物名 9 处同步世事.app.tar.gz(release notes 前缀改世事);MYIA_SIDECAR_SKIP 等 env 名不动
- 提交 a20882b(临时 index 管道,conf 里与并行改名 hunks 同 hunk 挤着——用 HEAD blob sed 出纯净视图再 diff 成补丁);共享 index 归位 reset

## 2026-10-03 图标 v3.1:星系形状重塑(task 10-03-rename-shishi 迭代,commit 598130f)

- 主人两轮反馈:「不是亮度,是形状」+「亮度不能太亮,要更符合现代宇宙」。v2 提亮方向作废,v3 重塑:双臂 269° 对数回旋(a=110,b=0.215,倾角-0.42)+窄臂收尖+椭圆核球;渲染三件套=暗尘盘垫底(压暗臂间)+尘带 0.40+细亮脊线 5px——低亮度下形状立住的正解;降饱和全项(muted 星云/核辉 r175/90 颗细星尘/3 个遥远小星系深场感)
- 数值验收(galaxy-only 渲染):臂/缝对比全半径 5.3-8.3x,缝隙亮度 55-86,眼描边 438 仍是主角;装机 icns 逐字节核验,killall Dock 刷缓存
- 两大坑记档:①像素采样器 Y 翻转乌龙吞掉两轮——CGBitmapContext 缓冲行序与 SVG 坐标同向,(h-1-y) 反转是画蛇添足;眼睛上下对称测不出翻转,臂/缝恰好镜像互换造成"形状糊了"假象 ②复合命令里的 cd 被环境剥离(连跑五次才定位),git 一律 git -C 绝对路径

- 主人验收图标 v3.1:「这次的看起来可以了」——10-03-rename-shishi 全部交付定稿(更名世事+眼后宇宙图标),任务维持 review 休止态

## 2026-10-03 YAML 编辑器 GUI 无头冒烟收口(task 10-03-yaml-editor,验收 13/13 全勾)

- 三轮环境缠斗后换道:①PyInstaller 共享缓存连续悬空(onnxruntime/cv2/numpy 三个不同 victim)→ 根因 = build-sidecar.sh `--clean` 先删共享缓存 × 并行会话同时在打包,互删互踩;核炸缓存冷构建一次通过后不再重打包,工作流改为 ping 现有二进制 ②主人明令禁止前台 GUI 自动化(osascript 抢焦点)→ 改无头方案:vite 真前端 + Playwright chromium + 自写 bridge.mjs(spawn 真实 sidecar serve,显式清空 MYIA_HOME 保持 dev 模式)+ addInitScript 注入 __TAURI_INTERNALS__ shim(invoke 约定与 client.ts 逐字段对齐)——真实 UI×真实二进制,零窗口零焦点
- 冒烟 9/9:AC1(stocks.yaml 135/135 行覆盖 100%、中文注释可见、完整路径)、AC2(改 schedule→Meta+S→「doctor 复核通过」;实测 diff 恰 1 增 1 删、注释全为上下文、.bak 留底)、新建 my-smoke→sources 即时可见→删除往返;6 张截图独立视觉复核全过;顺带真实验证坏文件徽标(并行会话在途 games.yaml 显示「损坏 unknown_field」)
- 工作流脚本级 diff 复查为空的归因:司机收工时 M plugins/stocks.yaml 在案(transcript.finalStatusPlugins),7 分钟后脚本复查已空 = 并行会话 e2e 清场 git checkout plugins/ 竞态还原;以司机实测快照(.zcode/smoke/yaml-editor/git-diff-stocks.txt)+ pytest 双证定案,不误判产品缺陷
- 自我披露两笔:①脚本 verified[] 模板无条件写「仅 1 增 1 删」文案(真值是未过)——报告模板 bug,已按真值改写;②首版冒烟确实 osascript 抢了前台(旧跑 10:07-10:33 痕迹),主人叫停后已换无头并停旧跑
- 验收 1-13 全勾;task 留 in_progress 等主人过目截图后 finish-work。工程债登记:打包缓存竞态(锁或独立 PYINSTALLER_CONFIG_DIR)与并行 e2e 清场互踩,建议归 release 工程/ci-gates 侧修

## 2026-10-03 v1.1.1 装机冒烟收尾 + 双任务转 review(task 10-03-v111-release / 10-03-docs-truth)

- 装机冒烟:重装 .app 首跑「运行第一个插件」demo 出真数据 **30 条**,零 config_error——PRD 验收「demo 随包首跑真数据」实证
- 五屏截图入库 docs/screenshots/(均远超 30KB 门槛):dashboard.png 310KB / settings.png 236KB / sources.png 196KB / feed.png 193KB / logs.png 179KB;README.md:198-204/502-508 死链随本批解除
- 版本四处核验 1.1.1(pyproject:7 / myia-classifier:7 / Cargo.toml:3 / tauri.conf.json:5,Cargo.lock myia-desktop 同步);CHANGELOG 1.1.1 条目含数据通路修复;README 徽章 1.1 stable + Gatekeeper 右键指引(中英)齐备
- PRD 勾 4/9:版本一致/CHANGELOG/README 升格/demo 首跑真数据;未勾 5 项=updater 密钥校验(主人三密钥)、密钥扫描(留终检)、tag/Release(主人 Secrets 门禁)、PyPI(主人凭据门禁)、四帖素材(截图已备,文案随 R2-4)
- 遗留口径出入:README.md:277 B2/B3/B4 去向写「排 v1.2」,PRD 回标注记要求写「v1.1.2 桌面对齐批次」或去版本号化——发布前宜顺手改一行
- 两任务 task.json 直改 review(不走 task.py finish,防跨会话指针踩踏);代码主体在 fbba437 链,本流程只补截图+簿记
