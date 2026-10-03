import { AlertTriangle, CircleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import type { YamlFinding } from "./api";

interface FindingsPanelProps {
  /** 校验/写回的 finding 清单(空 = 通过;渲染时 error 级排前) */
  findings: YamlFinding[];
  /** findings 为空时的通过文案(如「校验通过」/「保存时无警告」) */
  emptyText: string | null;
  className?: string;
}

/**
 * 校验结果面板:path + code + message 结构化清单,error/warning 分级
 * (error=destructive 拦保存;warning=琥珀,如 secret_unknown 不拦保存)。
 * findings 为空且 emptyText 给定 → 一行通过态(不伪装成没跑过)。
 */
export function FindingsPanel({ findings, emptyText, className }: FindingsPanelProps) {
  if (findings.length === 0) {
    if (emptyText === null) return null;
    return (
      <p
        role="status"
        data-testid="findings-empty"
        className={cn("text-xs text-ok", className)}
      >
        {emptyText}
      </p>
    );
  }

  const ordered = [...findings].sort((a, b) =>
    a.level === b.level ? 0 : a.level === "error" ? -1 : 1,
  );

  return (
    <ul className={cn("flex flex-col gap-1", className)} data-testid="findings-panel">
      {ordered.map((finding, index) => (
        <li
          key={`${finding.path}:${finding.code}:${index}`}
          className="flex items-start gap-2 text-xs"
          data-level={finding.level}
        >
          {finding.level === "error" ? (
            <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-destructive" />
          ) : (
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" />
          )}
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="flex items-center gap-1.5">
              <Badge variant={finding.level === "error" ? "destructive" : "warning"}>
                {finding.level === "error" ? "错误" : "警告"}
              </Badge>
              <span className="truncate font-mono text-[11px] text-muted-foreground" title={finding.path}>
                {finding.path}
              </span>
              <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                {finding.code}
              </span>
            </span>
            <span
              className={cn(
                "break-all",
                finding.level === "error" ? "text-destructive" : "text-warning",
              )}
            >
              {finding.message}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}
