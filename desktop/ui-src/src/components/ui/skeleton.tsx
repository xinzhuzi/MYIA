import * as React from "react";

import { cn } from "@/lib/utils";

/** 骨架占位(加载态;C 阶段接真实数据后沿用) */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("animate-pulse rounded-md bg-accent", className)}
      {...props}
    />
  );
}

export { Skeleton };
