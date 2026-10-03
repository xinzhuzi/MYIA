import { useEffect, useState } from "react";
import { ChevronRight, Search } from "lucide-react";
import { NavLink, useLocation } from "react-router-dom";

import { GlobalRun } from "@/components/layout/global-run";
import { resolveNav } from "@/components/layout/sidebar";
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

/** 命令位快捷键文案:macOS ⌘K,其余平台 Ctrl K(桌面跨平台惯例) */
const COMMAND_KEY =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform ?? "") ? "⌘K" : "Ctrl K";

/**
 * 顶栏(D4 壳层对标 Linear):左侧面包屑(工作区 › 分组 › 页面,与侧栏
 * 导航同一解析口径),右侧品类全局过滤 + 全局跑一次 + 全局命令位。
 * sidecar 状态已迁侧栏底部账户区(Linear 底部状态区)。
 */
export function TopBar({
  category,
  onCategoryChange,
}: {
  category: string | null;
  onCategoryChange: (next: string | null) => void;
}) {
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
      <Breadcrumb />

      <div className="flex-1" />

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

      <GlobalRun category={category} />
      <CommandSlot />
    </header>
  );
}

/** 面包屑:世事 › 分组 › 页面(当前页为末段,foreground 中字重;前段弱色) */
function Breadcrumb() {
  const location = useLocation();
  const nav = resolveNav(location.pathname);
  if (!nav) return null;
  return (
    <nav aria-label="面包屑" className="flex min-w-0 items-center gap-1">
      <NavLink
        to="/"
        className="text-xs text-muted-foreground transition-colors duration-(--duration-fast) ease-out-expo hover:text-foreground"
      >
        世事
      </NavLink>
      {nav.group ? (
        <>
          <ChevronRight className="size-3 shrink-0 text-muted-foreground/50" aria-hidden />
          <span className="text-xs text-muted-foreground">{nav.group}</span>
        </>
      ) : null}
      <ChevronRight className="size-3 shrink-0 text-muted-foreground/50" aria-hidden />
      <span className="truncate text-sm font-medium text-foreground">{nav.entry.label}</span>
    </nav>
  );
}

/**
 * 全局命令位(D4 明确「留白」):只预留位置与视觉锚点(Linear 顶栏搜索/
 * 命令触发器形态),命令面板属功能件归后续任务,此处非交互、对辅助技术隐藏。
 */
function CommandSlot() {
  return (
    <div
      aria-hidden
      title="全局命令位:命令面板属后续版本,此处仅预留位置"
      className="flex h-7 w-44 select-none items-center gap-1.5 rounded-md border border-border/60 bg-muted/30 px-2 text-xs text-muted-foreground/70"
    >
      <Search className="size-3 shrink-0" />
      <span className="flex-1 truncate">搜索或跳转…</span>
      <kbd className="rounded-sm border border-border/60 bg-muted px-1 font-mono text-2xs leading-4 text-muted-foreground">
        {COMMAND_KEY}
      </kbd>
    </div>
  );
}
