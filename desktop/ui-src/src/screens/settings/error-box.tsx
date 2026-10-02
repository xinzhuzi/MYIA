import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { SidecarRequestError } from "@/lib/api";

interface ErrorBoxProps {
  error: SidecarRequestError;
  /** 重试动作(缺省不显示按钮) */
  onRetry?: () => void;
  retrying?: boolean;
}

/** 结构化错误态:sidecar 协议错误必有 code/path/message,全量如实展示。 */
export function ErrorBox({ error, onRetry, retrying = false }: ErrorBoxProps) {
  return (
    <div
      role="alert"
      className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <p className="flex items-center gap-1.5 font-medium text-destructive">
            <AlertTriangle className="size-4" />
            {error.message}
          </p>
          <p className="font-mono text-xs text-muted-foreground">
            code={error.code} path={error.path}
          </p>
        </div>
        {onRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry} disabled={retrying}>
            重试
          </Button>
        ) : null}
      </div>
    </div>
  );
}
