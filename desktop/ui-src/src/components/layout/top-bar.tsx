import { Loader2, PlugZap, Unplug } from "lucide-react";

import { useSidecarStatus } from "@/hooks/use-sidecar-status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/**
 * 顶栏:左侧品类选择(全局过滤器),右侧 sidecar 全局状态。
 * 品类选项来自插件清单(health 方法),C 阶段接入;骨架先立交互位。
 */
export function TopBar() {
  const { status, info, error, reprobe } = useSidecarStatus();

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border bg-background/80 px-4">
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">品类</span>
        <Select defaultValue="all">
          <SelectTrigger size="sm" className="w-40" aria-label="品类选择">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部品类</SelectItem>
            {/* 品类清单数据接入(C 阶段):api.health() → plugins[].id */}
          </SelectContent>
        </Select>
      </div>

      <div className="flex-1" />

      <SidecarStatusBadge status={status} info={info} error={error} onRetry={reprobe} />
    </header>
  );
}

function SidecarStatusBadge({
  status,
  info,
  error,
  onRetry,
}: {
  status: ReturnType<typeof useSidecarStatus>["status"];
  info: ReturnType<typeof useSidecarStatus>["info"];
  error: ReturnType<typeof useSidecarStatus>["error"];
  onRetry: () => void;
}) {
  if (status === "online") {
    return (
      <Badge variant="ok" className="gap-1.5 px-2 py-1" title={`协议 v${info?.protocol}`}>
        <span className="relative flex size-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-ok opacity-60" />
          <span className="relative inline-flex size-2 rounded-full bg-ok" />
        </span>
        sidecar v{info?.version} · 协议 v{info?.protocol}
      </Badge>
    );
  }

  if (status === "connecting") {
    return (
      <Badge variant="unknown" className="gap-1.5 px-2 py-1">
        <Loader2 className="size-3 animate-spin" />
        连接 sidecar…
      </Badge>
    );
  }

  return (
    <div className="flex items-center gap-1">
      <Badge
        variant="warning"
        className="gap-1.5 px-2 py-1"
        title={error ? `${error.code} @ ${error.path}: ${error.message}` : undefined}
      >
        <Unplug className="size-3" />
        {error?.code === "sidecar_unavailable" ? "未在 Tauri 环境中" : "sidecar 未连接"}
      </Badge>
      <Button
        variant="ghost"
        size="icon"
        className="size-6"
        title="重新探测 sidecar"
        onClick={onRetry}
      >
        <PlugZap className="size-3" />
      </Button>
    </div>
  );
}
