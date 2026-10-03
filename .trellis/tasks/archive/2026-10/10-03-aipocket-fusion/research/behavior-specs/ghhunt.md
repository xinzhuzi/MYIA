# GitHub 工件猎取流程规格(ghhunt)

> 行为规格——AGPL 只读观察产出,实现时只认本规格不回看源码。
> 源码:aipocket-main(Rust)。证据指针 file:line 均相对该仓库。

## 1. 查询从哪来
- 查询 = 20 个 `ProviderPack` 的 `github_terms` 数组拼接去重(packs.rs:9-197;main.rs:276-280、api/routes.rs:1482-1489)。例:`sk- filename:.env`(openai)、`GEMINI_API_KEY`(gemini)。
- 注意:任务假设"21 个包",实测 `PACKS` 为 **20 个**(packs.rs `id:` 计数=20)。25 个供应商的验证规格在 prober/provider.rs(见 fingerprints.md)。
- 三条"泳道"枚举 `GithubLane`:CommitMessage / CodeSnapshot / SeededFileHistory(github_artifacts.rs:118-124)。**实况**:SeededFileHistory 与分片机制 `code_shards`(github_artifacts.rs:173-209)、补丁解析入口 `extract_patch`(:280)在本快照只有定义与测试引用,主扫描循环未接线(全仓 grep 证实;标 `未核实` 是否其它入口调用)。

## 2. GitHub API 用法(clients/github.rs)
- 端点:`GET /search/code?q&page&per_page`(code 泳道,:133-135)、`GET /search/commits`(commit 泳道,只取第 1 页,sources.rs:473)、`GET /repos/{o}/{r}/commits/{sha}`(取 commit.message,scanner.rs:815-820)、`GET /repos/{o}/{r}/git/blobs/{sha}`(取 base64 文件内容,:824)、`GET /rate_limit`(:105)。`file_history`(:168)已定义未接线。
- 每请求头:`Authorization: Bearer <token>`、`X-GitHub-Api-Version: 2022-11-28`、`Accept: application/vnd.github+json`(github.rs:38-44;config.rs:187-188)。
- 认证:token 池 `github_token_list()`,**无 token 直接报错**("GITHUB_TOKENS not configured",github.rs:50,56);且无 token 时 `GithubSource` 根本不注册(main.rs:270-273,还需 PG 启用)。
- 多 token 轮转:原子计数器取起始下标,每轮依次尝试全部 token(github.rs:54-93)。
- 限速:仅 403/429 触发降级;读 `retry-after` 头,否则 `x-ratelimit-reset - now`;等待值+1 秒后**仅重试一轮**,且等待须 ≤ `github_rate_limit_max_wait_seconds`(默认 90s,config.rs:203),否则报错放弃(github.rs:87-102)。非 403/429 状态码直接失败。
- 分页上限:code 泳道硬编码 **1..=5 页**(sources.rs:428),`per_page` 默认 100(config.rs:191);返回条数 < per_page 提前停(:460-462)。commit 泳道条数上限 = `github_commit_query_budget`(默认 12)与查询数取小(sources.rs:471-473;config.rs:189)。
- 断点:每 (pack,query,lane) 存 `next_page` 游标(sha1(shard_id),sources.rs:420-427,590-614),跑完即重置为 1(:464-469)。

## 3. 统一 diff 解析(parse_unified_patch,github_artifacts.rs:21-78)
- 跳过行:以 `---`、`+++`、`diff `、`index `、`\` 开头的行(github_artifacts.rs:27-34)。
- hunk 头正则:`^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@`,捕获 old/new 起始行号,重置两个行号计数器(:22,35-38)。
- 行分类:首字符前缀按 UTF-8 字符宽度剥离——`+`→`PatchSide::Added`(记录 `new_lineno`,`old_lineno=None`,new 计数+1);`-`→Removed(old 计数);空格/空行→Context(两计数器同进)(:45-73)。其它首字符丢弃(:74)。
- **更正任务假设:并非"只取 Added"**。`extract_patch`(:280-327)按 Added→Removed→Context 三侧分别聚合文本跑指纹,`change_side` 标注 "added"/"removed"/"context";`line_start/line_end` = 该侧所选行行号的最小/最大值(非命中行精确行号,:310-323)。行为动机:被删除行或未变更行中的密钥可能仍然存活。但如上,该函数未接入实况管线;实况两条泳道均以 `change_side="context"` 全文扫描(sources.rs:498-505,575-581;scanner.rs:860-864)。

## 4. 凭证匹配与归因(extract_artifact_text,github_artifacts.rs:244-279)
- 单一联合正则(十大密钥族,:252):`sk-`(≥16)、`AIza`(≥20)、`gsk_`、`nvapi-`、`r8_`、`xai-`、`ksk_`(≥16)、`crsr_`(≥32)、`pt-`(≥16)、`ABSK`(≥20,字符类含 `+=/.`)。同文本内按命中串去重(:253-256)。
- apiurl 归因:调用方给了 endpoint(通常是命中项 html_url)则直接用;否则按**密钥前缀**映射静态官方地址:`xai-`→api.x.ai/v1、`ksk_`→app.kiro.dev、`crsr_`→api.cursor.com、`pt-`→api.qoder.com、`ABSK`→bedrock.us-east-1、`AIza`→generativelanguage.googleapis.com;再否则若上下文含 `windsurf_service_key`/`codeium_service_key` → server.codeium.com/api/v1;都没有则为空串(:221-242)。`sk-`(openai)无前缀分支,故依赖命中项 html_url 或留空。
- 产出 `ExtractedArtifactSecret`(:211-220,244-279):credential{apikey, apiurl, source="github", source_type(=code_snapshot/commit_message), backend="github", raw_context=前 2048 字符} + source_kind + change_side + file_path + object_sha + line_start/end。上游再包 `CredentialObservation`,附 provenance:repository_id/full_name、commit_sha、object_sha、file_path、query_id、lane(sources.rs:653-691)。置信度概念不存在——无打分,只有来源元数据。

## 5. 噪音过滤(is_noise_artifact_path,github_artifacts.rs:79-116)
- basename 以 `.env` 开头 → **永不判噪**(保留 .env.example)。路径含 examples/example/samples/fixture(s)/testdata/test-data/test_data/mocks/mock/docs/documentation/changelog 目录段、或含 provider-catalog/official-external-provider/openclaw.plugin.json/catalog.json/catalog.toml、或 basename 以 .md 结尾、或以 readme/changelog/license/contributing 开头 → 丢弃。code 泳道在扫描时先做此过滤(sources.rs:550-553)。

## 6. 两条实况泳道的完整行为(sources.rs)
- code_snapshot:逐条 item 校验公开性(`/repository/private==false` 或 `visibility=="public"`,:541-548)→ 噪音路径过滤 → 基础文本 = `text_matches` 的 JSON 串;若能取到 blob sha 则 GET blobs 接口、base64 解码、**解码后 ≤1 MiB 才采用**(:562-574)。跑指纹产出观察 + host_hit,并每条 item 入队 `ArtifactWork`(work_status=fetch_pending,:616-645)。
- commit_message:仅 `repository/private==false` 的 item(:486-489);对 `commit.message` 全文跑指纹(source_kind=commit_message,change_side=context,endpoint=html_url);同时入队 ArtifactWork。
- 工件二次加工(scanner.rs:306-313,794-899):扫描后从队列领 ≤200 条;commit 类取 `/commits/{sha}` 的 message,blob 类取 blobs 接口并受 `github_max_blob_bytes`(默认 1 MiB,config.rs:202)限制;并发 = `github_artifact_concurrency` 默认 8。超限错误归类 `artifact_too_large`,其它错误 `transient`,成功 `terminal`(重试上限 5,:871-895)。

## 7. 边界情形
- 超大 diff/blob:blob 解码 >1 MiB 即弃(sources.rs:570;scanner.rs:836-838),无截断降级。
- 二进制文件:无专门检测,blob 以 `from_utf8_lossy` 容错解码后照常跑正则(scanner.rs:839)。
- 空 diff/空文本:正则无命中,产出空集,不报错。
- 无 token:该源不启用(见 §2)。
- 搜索 API 报错:记入 errors、code 泳道 break 出该查询的分页循环,继续下一查询(sources.rs:430-434)。

## 未核实
- `GithubQueryShard.build_q` 的限定词组装与 `assert_invariants`(commit 泳道禁 path:/filename:/is:private、自动补 is:public,github_artifacts.rs:139-171)未在主循环出现——主循环直接用 pack 原始查询串。
- `github_lookback_hours`(24)/`full_lookback_hours`(720)等时间窗设置(config.rs:193-196)未在已读文件中见到消费点,疑为预留。
- commit 泳道虽定义了 lane,但 `search_commits` 用的查询串未自动追加 `is:public`,公开性靠结果过滤(sources.rs:486-489)。
