# 真跑验证证据 — credhunter 冒烟(10-03-aipocket-fusion)

> 日期:2026-10-03 · 执行:真跑验证员(沙箱 `MYIA_HOME=/tmp/myia-credhunter-smoke`,不碰真实数据根;每步限速礼貌)
> 证据文件只留掩码形态,不外发;门禁(全量 pytest/ruff/shishi 演练)由脚本统一跑,此处不含。

## 结论一览

| 步骤 | 结果 | 依据 |
|---|---|---|
| 1. credhunt(GitHub token 真跑) | **ownerSide**(无 token) | 钥匙串只读复核:仅 `myia/image/api_key`,无 `myia/credhunter/github-token` |
| 2. credcheck 401 判死路径 | **ran,全绿** | 单次真探测 deepseek models 端点,401→expired→rejected |
| 3. exposure 无 key 空态 | **ran,全绿** | 沙箱 dry-run:exposure 源 `credential_missing` 结构化空态,exit 0 无 failures |
| 附. credcheck 活 key 路径 | **ownerSide**(主人活 key 未备) | PRD AC2/Q10:活键冒烟需主人自备活 key |

## 1. credhunt — ownerSide(缺 GitHub token)

- 只读复核命令:`MYIA_HOME=/tmp/myia-credhunter-smoke .venv/bin/shishi secret list`
  输出:`钥匙链凭据 1 个:myia/image/api_key`(与脚本所查一致;值不可读出)。
- credhunter 域 token(`myia/credhunter/github-token`)未配置 → 按口径记 ownerSide,不做真跑。
- 佐证(零出网):`shishi credhunt --json` → `{"error": "tokens_missing", "message": "GitHub token 池为空:无 token 该源不启用(显式报错,不静默)"}`,exit 1 —— 与 ghhunt.md §2「无 token 该源不启用」语义一致。
- 达标动作(主人侧):`myia secret set myia/credhunter/github-token` 后,品类 YAML 源写
  `github_tokens: ["keychain:myia/credhunter/github-token"]`(引擎面),或 CLI `shishi credhunt --github-token keychain:... --json`。

## 2. credcheck 401 判死路径 — ran,全绿

- 方法:模块函数真跑(`check_credential`,经 `import_credhunter_adapter("plugins")` 加载适配器;与 CLI 同一加载通路)。
- 输入:合成假 key(真前缀 `sk-` + 假串,掩码 `sk-0f1e2…MASKED…e1f0`)+ `apiurl=https://api.deepseek.com`(域名归因)。
- 真实出网:**单次** `GET https://api.deepseek.com/v1/models`(Bearer,15s 超时);`probe_balance=False`(Q7 默认关,**零余额/身份端点请求**)。
- 断言(6/6 通过,详见 [credcheck-401-death-path.json](credcheck-401-death-path.json)):
  - `status_code=401` → `key_state=expired`(credcheck.md §3「credential expired or revoked」)
  - `validation_state=rejected`、`error=auth_denied`(§2.6 三态判死)
  - `balance=None`;全文假键零出现在任何输出字段(掩码-only,Q9)

## 3. exposure 无 key 空态 — ran,全绿

- 命令:`MYIA_HOME=/tmp/myia-credhunter-smoke .venv/bin/shishi run plugins/exposure.yaml --dry-run --json`(exit 0)。
- 断言(7/7 通过,详见 [exposure-empty-state.json](exposure-empty-state.json)):
  - run `status=success`、`failures=[]`;
  - `exposure-scan` 源:`skipped=true, skip_reason=credential_missing, item_count=0, failed=false, error=null` —— 结构化空态,不报错不静默(AC6);stderr 有凭据解析失败 warning 留痕;
  - 同品类 `manual-triage`(本地 scan lane,零出网)照常产出 1 条,item 内 `apikey_masked` 与 `mask_apikey()` 逐字符一致(前 8 后 4),全文合成键零泄漏;
  - push 走 stdout 且 `dry_run=true` 不实发(Q9)。

## 附. 未跑项(主人侧)

- **credcheck 活 key 路径**(AC2/Q10「主人自备活 key:final_verified + 余额正确」):主人活 key 未备,记 ownerSide;余额探测矩阵(13 家匿名端点)亦随此待主人 key 显式 `--balance` 验证。
- **FOFA/Shodan 有 key 路径**:钥匙串无 `myia/credhunter/fofa-key`/`shodan-key`,真跑待主人自备(AC6 空态路径本次已验证)。
