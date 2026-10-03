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
