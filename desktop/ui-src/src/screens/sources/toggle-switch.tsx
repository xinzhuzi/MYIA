import { cn } from "@/lib/utils";

/**
 * 启停开关(本屏私有;共享 components/ui 无 switch,按边界不新增共享件)。
 * 语义 = WAI-ARIA switch:role="switch" + aria-checked;启 = 品牌青轨。
 * pending 时禁点并降低不透明度(写回/复核期间防抖)。
 */
interface ToggleSwitchProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  /** 无障碍名(中文,如「停用 源xx」) */
  ariaLabel: string;
  className?: string;
}

export function ToggleSwitch({
  checked,
  onCheckedChange,
  disabled = false,
  ariaLabel,
  className,
}: ToggleSwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-4.5 w-8 shrink-0 items-center rounded-full border border-border transition-colors",
        "focus-visible:ring-[3px] focus-visible:ring-ring/40 focus-visible:outline-none",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "border-primary/40 bg-primary/70" : "bg-muted",
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "pointer-events-none block size-3.5 rounded-full bg-foreground/80 shadow-sm transition-transform",
          checked ? "translate-x-4 bg-background" : "translate-x-0.5",
        )}
      />
    </button>
  );
}
