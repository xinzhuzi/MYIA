import { CheckCircle2, CircleDashed, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { DoctorVerify } from "./api";

/** 凭据存在性三态(值不可读;exists=null=无钥匙链后端无法核验) */
function CredentialState({ exists }: { exists: boolean | null }) {
  if (exists === true) {
    return (
      <span className="flex items-center gap-1 text-ok">
        <CheckCircle2 className="size-3.5" />
        已在钥匙链
      </span>
    );
  }
  if (exists === false) {
    return (
      <span className="flex items-center gap-1 text-destructive">
        <XCircle className="size-3.5" />
        缺失(需写入)
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 text-unknown">
      <CircleDashed className="size-3.5" />
      无法核验(无钥匙链后端)
    </span>
  );
}

interface DoctorVerifyPanelProps {
  verify: DoctorVerify | null;
  loading: boolean;
}

/**
 * doctor 验证回显面板:保存凭据后的存在性核验 + 现值/发现展示。
 * 一切回显都来自 doctor 应答(结构化事实),界面不从本地状态回显任何值。
 */
export function DoctorVerifyPanel({ verify, loading }: DoctorVerifyPanelProps) {
  if (loading && verify === null) {
    return (
      <div className="flex flex-col gap-2" aria-label="doctor 验证中">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-6 w-2/3" />
      </div>
    );
  }
  if (verify === null) return null;

  return (
    <div className="flex flex-col gap-3" data-testid="doctor-verify">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium text-foreground">doctor 回显</p>
        <Badge variant={verify.healthy ? "ok" : "destructive"}>
          {verify.healthy ? "无 error 级发现" : "存在 error 级发现"}
        </Badge>
      </div>

      <section className="flex flex-col gap-1.5">
        <p className="text-xs font-medium text-muted-foreground">
          钥匙链凭据(myia/ 命名;值不可读,只核验存在性)
        </p>
        {verify.credentials.length === 0 ? (
          <p className="text-xs text-muted-foreground">暂无 myia/ 钥匙链引用</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {verify.credentials.map((entry) => (
              <li
                key={`${entry.kind}:${entry.name}`}
                className="flex flex-wrap items-center justify-between gap-2 rounded-sm bg-muted/30 px-2 py-1 text-xs"
              >
                <span className="font-mono text-foreground">{entry.name}</span>
                <CredentialState exists={entry.exists} />
              </li>
            ))}
          </ul>
        )}
      </section>

      {verify.enrichSections.length > 0 ? (
        <section className="flex flex-col gap-1.5">
          <p className="text-xs font-medium text-muted-foreground">
            LLM enrich 现值(base_url/api_key 为凭据,不出协议面)
          </p>
          <ul className="flex flex-col gap-1">
            {verify.enrichSections.map((view) => (
              <li key={view.pluginFile} className="flex flex-wrap items-center gap-2 text-xs">
                <span className="truncate font-mono text-muted-foreground" title={view.pluginFile}>
                  {view.pluginFile}
                </span>
                <span className="text-foreground">model={view.enrich.model}</span>
                <Badge variant={view.enrich.enabled ? "default" : "outline"}>
                  {view.enrich.enabled ? "已启用" : "未启用"}
                </Badge>
                <span className="text-muted-foreground">
                  batch={view.enrich.batch} · 预算/run={view.enrich.budget_per_run}
                  {view.enrich.cache_rows !== undefined && view.enrich.cache_rows !== null
                    ? ` · 缓存 ${view.enrich.cache_rows} 行`
                    : ""}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {verify.pushChannels.length > 0 ? (
        <section className="flex flex-col gap-1.5">
          <p className="text-xs font-medium text-muted-foreground">品类推送通道(品类 YAML push: 声明)</p>
          <ul className="flex flex-col gap-1">
            {verify.pushChannels.map((view) => (
              <li key={view.pluginFile} className="flex flex-wrap items-center gap-2 text-xs">
                <span className="truncate font-mono text-muted-foreground" title={view.pluginFile}>
                  {view.pluginFile}
                </span>
                {view.channels.length > 0 ? (
                  <span className="flex items-center gap-1">
                    {view.channels.map((channel) => (
                      <Badge key={channel} variant="secondary">
                        {channel}
                      </Badge>
                    ))}
                  </span>
                ) : (
                  <span className="text-muted-foreground">未声明</span>
                )}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {verify.proxyPools.length > 0 ? (
        <section className="flex flex-col gap-1.5">
          <p className="text-xs font-medium text-muted-foreground">代理池探测(doctor --config)</p>
          <ul className="flex flex-col gap-1">
            {verify.proxyPools.map((pool, index) => (
              <li
                key={`${pool.pool ?? index}-${index}`}
                className="flex flex-wrap items-center gap-2 rounded-sm bg-muted/30 px-2 py-1 text-xs"
                data-testid="proxy-pool-row"
              >
                <span className="font-mono text-foreground">{pool.pool ?? "?"}</span>
                {pool.ok === true ? (
                  <Badge variant="ok">连通{typeof pool.latency_seconds === "number" ? ` · ${pool.latency_seconds}s` : ""}</Badge>
                ) : pool.ok === false ? (
                  <Badge variant="destructive">不通</Badge>
                ) : (
                  <Badge variant="unknown">未探测</Badge>
                )}
                {pool.message ? <span className="truncate text-muted-foreground">{pool.message}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {verify.findings.length > 0 ? (
        <section className="flex flex-col gap-1.5">
          <p className="text-xs font-medium text-muted-foreground">结构化发现({verify.findings.length})</p>
          <ul className="flex flex-col gap-1">
            {verify.findings.map((finding, index) => (
              <li key={`${finding.code}-${index}`} className="flex flex-wrap items-start gap-2 text-xs">
                <Badge variant={finding.severity === "error" ? "destructive" : "warning"}>
                  {finding.severity === "error" ? "错误" : "警告"}
                </Badge>
                <span className="font-mono text-muted-foreground">{finding.scope}/{finding.code}</span>
                <span className="min-w-0 flex-1 text-foreground">{finding.message}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <p className="text-2xs text-muted-foreground">诊断时间:{verify.generatedAt}</p>
    </div>
  );
}
