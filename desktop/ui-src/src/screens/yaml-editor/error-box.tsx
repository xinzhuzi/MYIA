import { Button } from "@/components/ui/button";
import type { SidecarRequestError } from "@/lib/api";

interface ErrorBoxProps {
  error: SidecarRequestError;
  /** 重试动作(缺省不显示按钮;弹窗的 mtime_conflict 传「重读」) */
  onRetry?: () => void;
  /** 重试按钮文案(默认「重试」) */
  retryLabel?: string;
}

/**
 * 本屏私有错误框:与 sources 屏同款结构化呈现(code/path/message 全量如实)。
 * mtime_conflict 附「外部已改动,重读拉最新原文」指引(乐观锁冲突不覆盖外部改动)。
 */
export function ErrorBox({ error, onRetry, retryLabel = "重试" }: ErrorBoxProps) {
  return (
    <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs">
      <p className="font-medium text-destructive">{error.message}</p>
      <p className="font-mono text-2xs text-muted-foreground">
        code={error.code} path={error.path}
      </p>
      {error.code === "mtime_conflict" ? (
        <p className="text-2xs text-muted-foreground">
          文件在读取后被外部修改(CLI/别的窗口);「重读」拉取最新原文,本地未保存的修改将被放弃。
        </p>
      ) : null}
      {onRetry ? (
        <Button variant="outline" size="sm" className="mt-1" onClick={onRetry}>
          {retryLabel}
        </Button>
      ) : null}
    </div>
  );
}
