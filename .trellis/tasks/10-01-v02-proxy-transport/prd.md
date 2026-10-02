# fetch_base 代理 transport:单上游 HTTP/SOCKS5

## Goal

IP 池三层排期的第二层(grill Q4 定案):v0.1 只解析校验 `proxy:` 节,本任务让源级代理真正生效——桌面用户挂单一上游代理(国内抓 Yahoo/X 等源的刚需)。第三层(池轮换/住宅 IP)在 v0.3 随 myia-proxy plugin。

## Requirements

- fetch_base 实装 proxy transport:`proxy: direct` | `pool:<name>`(v0.2 先支持指向**单一上游代理**,配置声明如 `pools: { main: "http://user:pass@host:port" }` 或 `socks5://`;凭据 `env:`/`keychain:` 引用)
- httpx AsyncClient 级代理挂载;代理失败与源失败**错误分类区分**(代理挂 ≠ 源死,降级链决策依据不同)
- `residential:*` 维持「未实装」结构化报错(排期 v0.3)
- doctor 诊断项:代理连通性检测(v02-cli-full 消费)

## Acceptance Criteria

- [ ] 单测:direct/pool 选择、http 与 socks5 代理 URL 解析、代理失败错误分类
- [ ] 真实代理手动验证一次(本地代理即可,结果记任务日志,凭据不进仓库)

## Notes

- httpx 的 socks 支持需 `httpx[socks]` extra——若引入,在 PRD 记录核心依赖论证(httpx 依赖树只多 httpcore/socksio,可接受)
- 配置位置(pools 声明放品类 YAML 还是全局配置)design.md 定,倾向全局配置+品类引用
