import { useId, type ComponentProps, type ReactNode } from "react";

import { cn } from "@/lib/utils";

interface FieldInputProps extends ComponentProps<"input"> {
  /** 字段标签(中文;同时作为无障碍名) */
  label: string;
  /** 一行说明(引用规则/去向) */
  hint?: ReactNode;
  /** 校验错误(前端同口径校验,先挡一道) */
  error?: string | null;
}

/**
 * 表单字段(本屏私有;共享 components/ui 无 input,按边界不新增共享件)。
 * 密钥类字段由调用方传 type="password" + autoComplete="new-password"。
 */
export function FieldInput({ label, hint, error, className, ...props }: FieldInputProps) {
  const id = useId();
  const errorId = `${id}-error`;
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        className={cn(
          "h-8 w-full rounded-md border border-input bg-transparent px-2.5 text-sm outline-none",
          "placeholder:text-muted-foreground focus-visible:ring-[3px] focus-visible:ring-ring/40",
          "disabled:cursor-not-allowed disabled:opacity-50",
          error && "border-destructive/50",
          className,
        )}
        {...props}
      />
      {error ? (
        <p id={errorId} className="text-2xs text-destructive">
          {error}
        </p>
      ) : hint ? (
        <p className="text-2xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}
