import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

/** 徽章:default 品牌青;ok/warning/dead/unknown 对应源健康度四态 */
const badgeVariants = cva(
  "inline-flex w-fit shrink-0 items-center justify-center gap-1 whitespace-nowrap rounded-sm border px-1.5 py-0.5 text-xs font-medium [&>svg]:size-3",
  {
    variants: {
      variant: {
        default: "border-primary/25 bg-primary/15 text-primary",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "border-border text-muted-foreground",
        // destructive 文字用提亮红(WCAG 实算):#e5484d 在 destructive/15 底上
        // 仅 4.15~4.34(卡片/背景),#ff6b70 提到 5.42~6.14,全落面 ≥4.5
        destructive: "border-destructive/30 bg-destructive/15 text-[#ff6b70]",
        ok: "border-ok/30 bg-ok/15 text-ok",
        warning: "border-warning/30 bg-warning/15 text-warning",
        unknown: "border-unknown/30 bg-unknown/10 text-unknown",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

function Badge({
  className,
  variant,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return (
    <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { Badge, badgeVariants };
