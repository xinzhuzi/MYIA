import { Inbox, LayoutDashboard, Rss, Settings, Terminal } from "lucide-react";
import { NavLink } from "react-router-dom";

import { MyiaMark, MyiaWordmark } from "@/components/myia-mark";
import { cn } from "@/lib/utils";

/** 主导航项(to 与 App.tsx 路由一一对应;end=true 表示精确匹配) */
const NAV_ITEMS = [
  { to: "/", label: "仪表盘", icon: LayoutDashboard, end: true },
  { to: "/feed", label: "情报流", icon: Inbox, end: false },
  { to: "/sources", label: "源管理", icon: Rss, end: false },
  { to: "/logs", label: "采集日志", icon: Terminal, end: false },
  { to: "/settings", label: "设置", icon: Settings, end: false },
] as const;

/** 左侧导航:品牌头 + 五屏联动导航(NavLink 激活态由路由驱动)。 */
export function Sidebar() {
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-sidebar text-sidebar-foreground">
      <div className="flex items-center gap-2.5 px-4 pt-4 pb-3">
        <MyiaMark className="size-7" />
        <div className="flex flex-col">
          <MyiaWordmark className="text-sm leading-tight" />
          <span className="text-[10px] leading-tight text-muted-foreground">
            AI 替主人看着世界
          </span>
        </div>
      </div>

      <nav className="flex flex-1 flex-col gap-0.5 px-2 py-2" aria-label="主导航">
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                "flex h-8 items-center gap-2.5 rounded-md px-2.5 text-[13px] transition-colors",
                isActive
                  ? "bg-accent font-medium text-foreground"
                  : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
              )
            }
          >
            <Icon className="size-4 shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-border px-4 py-2.5 text-[10px] leading-relaxed text-muted-foreground">
        v1.1 骨架 · 业务接入见各屏空态
      </div>
    </aside>
  );
}
