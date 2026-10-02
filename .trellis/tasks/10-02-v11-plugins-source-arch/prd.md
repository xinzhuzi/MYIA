# plugins 插件层架构转向:git 源码管理 + 进程内功能接入(去 docker 化)

## 主人的决策(2026-10-02,原话要旨)

> plugins/ 里面的插件应该**被下载到这个文件夹下,使用 git 相关的链接管理他们的源码**,然后这个项目**使用他们的功能**,而不是一堆的 docker 内容。**我做的是桌面应用。**

本决策**部分取代** v0.3 plugin-market 的「local docker / remote 双模式」设计(该设计源自规划 v1.7 对桌面-Docker 矛盾的妥协;主人现明确:docker 不应是插件的主路径)。

## 现状问题(2026-10-02 核查)

- `plugins/myia-{proxy,osint,douyin,monitor,maxun}` 五包 = README + plugin.yaml + **docker-compose.yml**,纯 docker 指针,**不含也不引用任何上游源码**;`myia-credentials` 只有 README + plugin.yaml(remote 指针)
- 与产品定调冲突:〇.5「桌面用户不要求会 Docker」「开箱即用是桌面工具的生死线」——现在插件层的默认路径却全是 docker
- 「使用他们的功能」无从谈起:MYIA 没有任何进程内/子进程调用上游代码的通路

## 目标架构(design 阶段细化)

```
plugins/<name>/
├── plugin.yaml          # manifest(保留,演进现有 schema)
├── adapter.py           # MYIA 侧适配器:进程内 import 或子进程调用上游
├── vendor/<upstream>/   # 上游源码:git submodule(pin commit)或首用 git clone 下载
└── README.md            # 双路径:桌面(源码+进程内)/ 服务端(可选 compose,迁去 docker/)
```

### 六场景件功能分级(design 复核,先给可行性判断)

| 插件 | 上游 | 桌面进程化可行性 | 定级建议 |
|---|---|---|---|
| myia-osint | Photon 13.2k(GPL,纯 Python CLI) | **高**:submodule + 子进程调用 | 转源码型,首个样板 |
| myia-proxy | jhao104/proxy_pool(MIT) | **中**:其抓取/校验模块可进程内复用(去 Redis 化);完整服务形态留服务端 | 拆两档:内置轻量 fetcher + 可选服务 |
| myia-monitor | changedetection.io 34.7k | 已被 MYIA 内置变更指纹覆盖主场景 | **瘦身**:remote 集成降为可选,撤本地 compose |
| myia-credentials | aipocket(自部署) | 本就是 REST API | 维持 remote(与桌面兼容,零 docker) |
| myia-douyin | Douyin_TikTok_Download_API(重 Web 服务+浏览器) | 低 | 桌面默认集移出;留服务端可选 |
| myia-maxun | maxun(AGPL,Electron 应用) | 不可进程化 | 桌面默认集移出;留服务端可选或标注不支持 |

### docker 的去向

- 桌面路径(默认):**零 docker**——源码 submodule/clone + 进程内/子进程
- 服务端形态(docker/):保留 compose 作为可选部署路径,场景件的 compose 文件**迁入 docker/plugins/**(或删除),plugins/ 里不再出现 docker 内容

## Requirements

- 上游源码以 git 链接管理:优先 **git submodule**(pin commit,可审计可升级;许可证上让上游代码留在其自身仓库,比复制更干净);对不装 git 的极端用户提供 `myia plugin install` 内置 clone 下载到 vendor/ 的等价路径(design 定默认顺序)
- MYIA「使用功能」:适配器统一接口(design 定),进程内 import / 子进程两种接入方式;插件装不上/上游缺失→结构化降级,**铁律「不拦核心流水线」不变**
- 许可证红线:GPL(Photon)以 submodule 依赖方式使用、不复制代码进 MYIA 仓库;AGPL(maxun)不进桌面默认集
- plugin.yaml schema 演进(新增 vendor.source/adapter 描述),向后兼容或显式迁移 v0.3 测试语义
- 文档四同步:plugin.yaml、SKILL.md、docs、plugins 示例(变更纪律)

## Acceptance Criteria

- [ ] myia-osint 作为样板落地:submodule 指向 Photon pin commit,`myia osint <target>`(或等价 CLI/进程内入口)在本机零 docker 完成一次真实侦察(需主人指定合法目标,默认用 example.com)
- [ ] myia-proxy 轻量路径:进程内完成一次代理抓取+测活(零 Redis 零 docker);完整服务模式在 docker/ 可选
- [ ] monitor/credentials/douyin/maxun 按 定级建议 完成迁移或移出,plugins/ 目录里 grep 不到 docker-compose
- [ ] `myia plugin list` 正确展示新分级;装不上任一插件时核心流水线照常(铁律回归测试绿)
- [ ] 全量 pytest 绿;文档四同步;许可证边界复核记录在案

## Notes

- 排期:v1.1(与桌面正式版同批,「我是要做的桌面应用」——本转向是桌面版的配套基建);osint 样板可先行
- 关联:10-02-v11-plugins-layout 无关;本任务与 10-01-v03-plugin-market(已完成,review)是演进关系,不回滚其 loader/装卸 CLI 成果

> **2026-10-02 关联批注**:吸收 v11-low-plugins 子任务中的 README 修复项(重写时一并做);另注意 packages 套娃已拆(v11-packages-layout 完结),插件 vendor 布局勿再引入类似深层套娃。
