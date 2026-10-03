import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * shadcn/ui input(纯 input,无 Radix 依赖)。
 * 尺寸与 select-trigger/button 同族(h-8/rounded-md/text-sm);
 * 过渡统一走 D2 token(duration-(--duration-fast) + ease-out-expo);
 * 焦点环由 index.css 的全局 :focus-visible 体系统一提供。
 */

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "flex h-8 w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1 text-sm text-foreground",
        "placeholder:text-muted-foreground/70",
        "transition-[color,border-color,box-shadow] duration-(--duration-fast) ease-out-expo",
        "aria-invalid:border-destructive/60",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
