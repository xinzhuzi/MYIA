# 安全底线(铁律,规划 v1.7 定案)

## 凭据

- LLM key / 代理 key / 推送 token / 源 cookie 一律进系统钥匙链(macOS Keychain / Windows DPAPI)
- YAML 只许 `keychain:` / `env:` 引用;**配置文件出现明文凭据 = 启动即报错拒跑**
- 三态:`env:VAR` / `keychain:name` / CLI 首跑录入(`myia secret set`)后入钥匙链
- 钥匙串既有项更新被拒(macOS -25244 需 GUI 授权)时回落「删旧建新」重建:写前探测保留旧值、重建失败尽力回写(`set_secret`,10-03-image-fix-followups)
- 仓库红线(2026-10-01 grill Q8 提级):**仓库即公开,含 `.trellis/` 全部任务/PRD/research**——任何凭据、内网地址(127.0.0.1 例外)、生产语料、私有系统痕迹零容忍;任务文档自下笔起按公开标准撰写,验证记录只写「验证通过+日期」;唯一例外是标注「仅本地」的外部规划文档路径引用

## 网络

- 默认尊重 robots.txt;限速礼貌(qps/jitter/backoff)是默认行为不是选项
- 「真人验证+手机号」类源无解也不碰——结构化报错,不做绕过
- ingest/回调 API 默认关闭;开启强制 token 鉴权且仅绑本机/内网

## Plugin

- **任何 plugin 装不上,核心流水线必须照常跑通**(桌面工具的生死线)
- plugin remote 凭据入钥匙链;compose 文件零明文凭据
