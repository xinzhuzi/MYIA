import { Badge } from "@/components/ui/badge";
import type { SourceHealthState } from "@/lib/api";

/** 健康度 → 徽标三色(+unknown 中性灰):ok 绿 / degraded 琥珀 / dead 红。 */
const HEALTH_BADGE: Record<SourceHealthState, { variant: "ok" | "warning" | "destructive" | "unknown"; label: string }> = {
  ok: { variant: "ok", label: "正常" },
  degraded: { variant: "warning", label: "退化" },
  dead: { variant: "destructive", label: "失效" },
  unknown: { variant: "unknown", label: "未知" },
};

interface HealthBadgeProps {
  state: SourceHealthState;
  /** 悬浮提示:健康度评判原因(health.reason) */
  reason?: string;
  className?: string;
}

/** 源健康度三色徽标(数据语义:src/myia/cli.py evaluate_source_health)。 */
export function HealthBadge({ state, reason, className }: HealthBadgeProps) {
  const badge = HEALTH_BADGE[state];
  return (
    <span title={reason} className={className}>
      <Badge variant={badge.variant} data-health={state}>
        {badge.label}
      </Badge>
    </span>
  );
}
