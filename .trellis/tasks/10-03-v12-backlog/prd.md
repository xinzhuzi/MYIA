# v1.2 待办池:Windows 构建 + crawl4ai L3 实装 + proxy_pool 对接

## Goal

grill 决议 2026-10-03 Q1:发布工程(v1.1.1)先行,以下三项排 v1.2。本档是
**待办池**(非执行档):收边界、现状与验收指针;开工时逐项拆正式任务
(复杂项按规矩补 design/implement)。

## Requirements(三项边界)

1. **Windows 产物化**
   - 现状:`desktop-release.yml` 已有 windows-latest job(msi,**构建级验证、
     允许失败**);PyInstaller 不可交叉编译 → CI native matrix 是唯一路。
   - v1.2 目标:msi/exe 产物可用(真机安装冒烟,对齐 v1.1.1 的 macOS 验收
     口径:cwd 无关路径解析 + 首跑种子 + 五屏数据/空态)。
   - 开放:主人有无 Windows 真机/VM 做冒烟(拆任务时问)。
2. **crawl4ai L3 实装**
   - 现状:`src/myia/engines/crawl4ai.py` 为薄层(~10KB);v0.2 任务
     (10-01-v02-engine-crawl4ai)按其范围已毕,L3 降级档实质缺位。
   - v1.2 目标:真引擎进降级链(L2 失败→L3 接管),extras 可选依赖
     `myia[crawl4ai]`,测试走录制回放(CI 零外网,Python spec)。
3. **proxy_pool 对接**
   - 现状:fetch_base 有 143 处 proxy 引用,transport 单上游(v0.2 已毕,
     10-01-v02-proxy-transport);池化(轮换/健康检查/住宅 IP)未写。
   - v1.2 目标:池化抽象 + 至少一家服务商实装;**服务商与预算是主人决策**,
     拆任务时先问。

## Acceptance Criteria(池档口径)

- [ ] v1.2 规划时三项各自拆成正式任务并引用本档
- [ ] 本档不挂 in_progress(它是池,不是工)
