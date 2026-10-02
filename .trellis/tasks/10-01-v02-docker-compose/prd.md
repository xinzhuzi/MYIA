# 服务端形态:docker compose

## Goal

三形态之一(规划第五节):想 24h 跑的用户 `docker compose up`,同一份 YAML、同一个核心。

## Requirements

- `docker/docker-compose.yml`:myia 服务容器(python:slim + uv 安装或预构建镜像)、卷挂载 plugins/ 与数据目录、`myia run --loop` 常驻
- 配置经环境变量注入(FEISHU_*/TG_*/LLM key),**镜像与 compose 文件零明文凭据**
- 可选 web 面板服务占位(正式 Web UI 是后续版本,规划定位=旁观窗口)
- 时区处理(TZ 环境变量,影响 AM/PM 槽位与 cron)

## Acceptance Criteria

- [ ] compose up 后按 schedule 自动触发并推送成功(手动验证记录)
- [ ] 镜像构建 CI 化(GH Actions,发布到 ghcr)
- [ ] 服务端与本地 CLI 共用同一份插件 YAML 无需修改

## Notes

- 编排决策:不引入 n8n/Kestra(规划定案),容器内就是 APScheduler
