import { cn } from "@/lib/utils";
import type { SourceHealthState } from "@/lib/api";

/**
 * 健康度四态语义色(D2 token:--ok / --warning / --dead / --unknown,全屏统一):
 * Ant Table 状态徽章范式 —— 圆点 + 文字,不做胶囊底(暗色面上胶囊底噪,密集
 * 表格里点+字扫读更快)。评判原因(reason)走 title 悬浮提示。
 */
const HEALTH_STATE: Record<SourceHealthState, { dot: string; label: string }> = {
  ok: { dot: "bg-ok", label: "正常" },
  degraded: { dot: "bg-warning", label: "退化" },
  dead: { dot: "bg-dead", label: "失效" },
  unknown: { dot: "bg-unknown", label: "未知" },
};

interface HealthBadgeProps {
  state: SourceHealthState;
  /** 悬浮提示:健康度评判原因(health.reason) */
  reason?: string;
  className?: string;
}

/** 源健康度状态徽章(数据语义:src/myia/cli.py evaluate_source_health)。 */
export function HealthBadge({ state, reason, className }: HealthBadgeProps) {
  const health = HEALTH_STATE[state];
  return (
    <span
      data-health={state}
      title={reason}
      className={cn("inline-flex items-center gap-1.5 text-xs text-foreground", className)}
    >
      <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", health.dot)} />
      {health.label}
    </span>
  );
}
