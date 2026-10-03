import { useEffect, useState } from "react";
import { Loader2, PlugZap, Unplug } from "lucide-react";

import { useSidecarStatus } from "@/hooks/use-sidecar-status";
import { GlobalRun } from "@/components/layout/global-run";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";

/** 品类下拉选项(health().plugins 的 id 去重 + 名称回显;排序稳定) */
interface CategoryOption {
  id: string;
  label: string;
}

/**
 * 顶栏:左侧品类选择(全局过滤器,选中即服务端过滤情报流),右侧 sidecar
 * 全局状态。品类选项来自 health().plugins(C8,10-03-feed-ux 接线;
 * 加载失败静默收敛为「全部品类」单选项,不遮蔽界面)。
 */
export function TopBar({
  category,
  onCategoryChange,
}: {
  category: string | null;
  onCategoryChange: (next: string | null) => void;
}) {
  const { status, info, error, reprobe } = useSidecarStatus();
  const [options, setOptions] = useState<CategoryOption[]>([]);

  useEffect(() => {
    let cancelled = false;
    void api
      .health()
      .then((health) => {
        if (cancelled) return;
        // id 去重(目录内同 id 多文件时首现优先)+ 名称回显;装不上的
        // 插件 id=null 不入选项(选了也无数据可滤)
        const seen = new Map<string, string>();
        for (const plugin of health.plugins) {
          if (plugin.id && !seen.has(plugin.id)) {
            seen.set(plugin.id, plugin.name ?? plugin.id);
          }
        }
        setOptions(
          [...seen.entries()]
            .map(([id, name]) => ({ id, label: name }))
            .sort((a, b) => a.id.localeCompare(b.id)),
        );
      })
      .catch(() => {
        // health 失败不拦顶栏:保持「全部品类」可用,选中过滤自然空态
        if (!cancelled) setOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border bg-background/80 px-4">
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">品类</span>
        <Select
          value={category ?? "all"}
          onValueChange={(value) => onCategoryChange(value === "all" ? null : value)}
        >
          <SelectTrigger size="sm" className="w-40" aria-label="品类选择">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部品类</SelectItem>
            {options.map((option) => (
              <SelectItem key={option.id} value={option.id}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex-1" />

      <GlobalRun category={category} />
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
    // 界面不放开发期文案:在线态只留状态点,版本/协议退到 title 悬浮供排障
    // (排障三件套:sidecar 版 · 协议版 · app 版(C10;dev/CLI 场景未注入则省略))
    const appPart = info?.app_version ? ` · app v${info.app_version}` : "";
    return (
      <Badge
        variant="ok"
        className="px-2 py-1"
        aria-label="sidecar 已连接"
        title={`sidecar v${info?.version} · 协议 v${info?.protocol}${appPart}`}
      >
        <span className="relative flex size-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-ok opacity-60" />
          <span className="relative inline-flex size-2 rounded-full bg-ok" />
        </span>
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

  if (status === "respawning") {
    // 壳层自动重拉中(main.rs 退避序列):静待,动作按钮保持可点(提前手动拉起)
    return (
      <Badge variant="warning" className="gap-1.5 px-2 py-1" title="sidecar 已退出,壳层按退避自动重拉">
        <Loader2 className="size-3 animate-spin" />
        重拉 sidecar…
      </Badge>
    );
  }

  if (status === "dead") {
    // 自动重拉超限:唯一修复动作 = 手动拉起(reprobe 内含 sidecar_restart)
    return (
      <div className="flex items-center gap-1">
        <Badge
          variant="destructive"
          className="gap-1.5 px-2 py-1"
          title={error ? `${error.code}: ${error.message}` : undefined}
        >
          <Unplug className="size-3" />
          sidecar 已停止
        </Badge>
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          title="拉起 sidecar"
          aria-label="拉起 sidecar"
          onClick={onRetry}
        >
          <PlugZap className="size-3" />
        </Button>
      </div>
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
        title="重新探测 sidecar(失败时自动拉起)"
        onClick={onRetry}
      >
        <PlugZap className="size-3" />
      </Button>
    </div>
  );
}
