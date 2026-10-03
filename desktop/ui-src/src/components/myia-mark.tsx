import { cn } from "@/lib/utils";

/**
 * 世事品牌标(eye+universe)。事实源:desktop/branding/shishi-icon.svg
 * (本文件是 public/shishi-icon.svg 的原样拷贝,构建时随产物分发)。
 */
export function MyiaMark({ className }: { className?: string }) {
  return (
    <img
      src="/shishi-icon.svg"
      alt="世事"
      draggable={false}
      className={cn("size-8 select-none", className)}
    />
  );
}
