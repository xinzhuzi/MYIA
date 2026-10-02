import * as React from "react";

import { MyiaMark } from "@/components/myia-mark";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface EmptyStateProps {
  /** 空态标题(中文) */
  title: string;
  /** 一行说明:数据从哪来、何时接入 */
  description?: React.ReactNode;
  /** 底部动作(骨架阶段一般不用) */
  action?: React.ReactNode;
  /** 右上/顶部小徽标文案,如「C 阶段接入」 */
  tag?: string;
  /** compact:卡片内小号空态;默认:整屏居中大空态 */
  compact?: boolean;
  className?: string;
}

/**
 * 统一空态:品牌图标 + 渐变光晕 + 中文标题/说明。
 * 五屏骨架与卡片占位共用,保证信息架构先立、业务后接(C 阶段)。
 */
export function EmptyState({
  title,
  description,
  action,
  tag,
  compact = false,
  className,
}: EmptyStateProps) {
  const icon = <MyiaMark className={compact ? "size-8" : "size-14"} />;
  const halo = (
    <div
      aria-hidden
      className={cn(
        "pointer-events-none absolute rounded-full bg-gradient-to-br from-brand-from/20 to-brand-to/20 blur-2xl",
        compact ? "size-16" : "size-40",
      )}
    />
  );

  return (
    <div
      className={cn(
        "relative flex flex-col items-center justify-center gap-1.5 text-center",
        compact ? "gap-1 px-4 py-8" : "gap-2 px-6 py-16",
        className,
      )}
    >
      <div className="relative flex items-center justify-center">
        {halo}
        {icon}
      </div>
      <div className="flex items-center gap-2">
        <p className={cn("font-medium text-foreground", compact ? "text-sm" : "text-base")}>
          {title}
        </p>
        {tag ? <Badge variant="outline">{tag}</Badge> : null}
      </div>
      {description ? (
        <p className={cn("max-w-md text-muted-foreground", compact ? "text-xs" : "text-sm")}>
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
