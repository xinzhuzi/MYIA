import { cn } from "@/lib/utils";

/**
 * MYIA 品牌标(eye+radar)。事实源:desktop/branding/myia-icon.svg
 * (本文件是 public/myia-icon.svg 的原样拷贝,构建时随产物分发)。
 */
export function MyiaMark({ className }: { className?: string }) {
  return (
    <img
      src="/myia-icon.svg"
      alt="MYIA"
      draggable={false}
      className={cn("size-8 select-none", className)}
    />
  );
}

/** 品牌渐变字标(青→紫,取自 branding SVG 的 accent 渐变)。 */
export function MyiaWordmark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "bg-gradient-to-r from-brand-from to-brand-to bg-clip-text font-semibold tracking-wide text-transparent",
        className,
      )}
    >
      MYIA
    </span>
  );
}
