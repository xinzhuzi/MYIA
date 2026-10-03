import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * shadcn/ui scroll-area API,无 @radix-ui/react-scroll-area 依赖(任务红线不装包)。
 * 本仓滚动条已全局统一为 Linear 细滚动条(index.css ::-webkit-scrollbar),
 * 故此处不重造原生条,而是提供定向 overflow 的结构化 viewport
 * (data-slot 钩子供逐屏自动滚底/粘底等行为挂接,React 19 下 ref/onScroll
 * 直接走 div 原生 props)。ScrollBar 仅作占位视觉槽(API 对齐,渐次增强用)。
 */

function ScrollArea({
  className,
  orientation = "vertical",
  children,
  ...props
}: React.ComponentProps<"div"> & {
  orientation?: "vertical" | "horizontal" | "both";
}) {
  return (
    <div
      data-slot="scroll-area"
      data-orientation={orientation}
      className={cn(
        "relative",
        orientation === "vertical" && "overflow-x-hidden overflow-y-auto",
        orientation === "horizontal" && "overflow-x-auto overflow-y-hidden",
        orientation === "both" && "overflow-auto",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

function ScrollBar({
  className,
  orientation = "vertical",
  ...props
}: React.ComponentProps<"div"> & {
  orientation?: "vertical" | "horizontal";
}) {
  return (
    <div
      aria-hidden="true"
      data-slot="scroll-bar"
      data-orientation={orientation}
      className={cn(
        "pointer-events-none absolute flex touch-none select-none transition-colors duration-(--duration-fast) ease-out-expo",
        orientation === "vertical" && "top-1 right-1 bottom-1 w-2",
        orientation === "horizontal" && "right-1 bottom-1 left-1 h-2",
        className,
      )}
      {...props}
    />
  );
}

export { ScrollArea, ScrollBar };
