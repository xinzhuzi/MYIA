import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * shadcn/ui switch API,无 Radix 依赖(任务红线不装包):
 * button[role=switch] + 原生受控/非受控 + onCheckedChange。
 * 动效:轨道变色与滑块位移均 180ms(--duration-base)expo-out,无弹跳。
 */

function Switch({
  className,
  checked,
  defaultChecked,
  onCheckedChange,
  onClick,
  ...props
}: Omit<React.ComponentProps<"button">, "onChange"> & {
  /** 受控态(与 shadcn/Radix Switch 同名 API) */
  checked?: boolean;
  defaultChecked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
}) {
  const [internal, setInternal] = React.useState(defaultChecked ?? false);
  const isControlled = checked !== undefined;
  const isChecked = isControlled ? checked : internal;

  function handleClick(event: React.MouseEvent<HTMLButtonElement>) {
    onClick?.(event);
    if (event.defaultPrevented) return;
    if (!isControlled) setInternal((prev) => !prev);
    onCheckedChange?.(!isChecked);
  }

  return (
    <button
      type="button"
      role="switch"
      aria-checked={isChecked}
      data-state={isChecked ? "checked" : "unchecked"}
      data-disabled={props.disabled ? "" : undefined}
      onClick={handleClick}
      data-slot="switch"
      className={cn(
        "inline-flex h-[18px] w-8 shrink-0 items-center rounded-full border border-transparent p-[2px]",
        "transition-colors duration-(--duration-base) ease-out-expo",
        "data-[state=checked]:bg-primary data-[state=unchecked]:border-border data-[state=unchecked]:bg-secondary/90",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <span
        data-slot="switch-thumb"
        data-state={isChecked ? "checked" : "unchecked"}
        className={cn(
          "pointer-events-none block size-3.5 rounded-full bg-foreground/90 shadow-sm",
          "transition-transform duration-(--duration-base) ease-out-expo",
          "data-[state=checked]:translate-x-[14px] data-[state=unchecked]:translate-x-0",
        )}
      />
    </button>
  );
}

export { Switch };
