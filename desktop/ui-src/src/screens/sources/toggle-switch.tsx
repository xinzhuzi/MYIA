import { Switch } from "@/components/ui/switch";

/**
 * 启停开关:内部转接 Phase1 基件 ui/switch(本屏私有 API 不变:ariaLabel
 * 中文命名)。动效走基件 —— 轨道变色与滑块位移均 180ms(--duration-base)
 * expo-out,Linear 克制级,无弹跳;语义 = WAI-ARIA switch(role=switch +
 * aria-checked),启 = 品牌青轨;pending 时禁点并降不透明度(写回/复核期间防抖)。
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
    <Switch
      checked={checked}
      disabled={disabled}
      aria-label={ariaLabel}
      onCheckedChange={onCheckedChange}
      className={className}
    />
  );
}
