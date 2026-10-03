# CI 增加 Docker 镜像构建检查(PR 期 build-only)

## Goal

grill Q8 决议(2026-10-03,出自 10-03-tag-release 八问全按推荐):发布改 tag-only 后,Dockerfile 只有发 tag 才被真实构建——改坏了要等发布日才暴露,与当初 ci-gates 给 Rust 补 `cargo check` 同理。在 `ci.yml` 增加一个构建检查 job,把 Dockerfile 回归拦在 PR 期。

## 背景

- `docker-publish.yml` 经 10-03-tag-release 改为纯 `v*` tag 触发(主人纪律:tag=发布,main 推送零发布物)——副作用是 Dockerfile 在日常 push/PR 完全不被构建。
- ci.yml 现有四 job:test / ui-test / rust-check / ruff;镜像构建是唯一无 CI 覆盖的发布面。

## Requirements

1. `ci.yml` 新增 `docker-build` job:与现有 job 同触发(push main + PR),随仓库现有 CI 纪律走,不加 per-job paths 过滤(保持文件简单;构建有层缓存,增量成本可接受)。
2. 构建方式:`docker build .`(ubuntu runner 原生 amd64,不装 QEMU 不建多架构——语法/依赖错误是主拦截目标,arm64 特有问题不在覆盖面,如实注记)。
3. **零发布语义**:不 login、不 push、不打 tag——纯本地构建验证;与 10-03-tag-release 的"main 推送零发布物"纪律一致。
4. 失败红在 PR,即 Dockerfile 回归被门禁拦截。

## Acceptance Criteria

- [x] **AC1** `ci.yml` 新增 docker-build job,push main 与 PR 均触发;`docker build .` 成功为绿、失败为红。
- [x] **AC2** 全程无 registry login / push / metadata 步骤(零发布语义;与 docker-publish.yml 职责不重叠)。
- [x] **AC3** 仅 amd64 原生构建(无 QEMU/buildx 多架构),job 内注释注明覆盖边界(arm64 特有问题不在此 job 覆盖)。
- [x] **AC4** actionlint 过 `ci.yml`;改动仅 ci.yml(+ 本任务档)。

## 不做的事

- 不动 `docker-publish.yml`(发布语义归 10-03-tag-release)。
- 不做多架构构建、不推 GHCR、不加镜像漏洞扫描(远期按需另议)。

## Notes

- 轻量任务:PRD-only。执行时与 10-03-tag-release 无依赖关系,可并行也可其后;若在其后,注意两者都碰 `.github/workflows/`,提交分开、stage 各自文件。

## 验收勾档(2026-10-03 执行完毕,dwfrun-cc671057)

**AC1-AC4 全 passed**:ci.yml 新增 docker-build job(与既有 job 同触发,checkout+docker build .,零 login/push/metadata/QEMU,注释含覆盖边界与任务号);actionlint v1.7.7 对 ci.yml 零告警;git status 核对改动仅 ci.yml + 本任务档(实现员与质检员双核)。
