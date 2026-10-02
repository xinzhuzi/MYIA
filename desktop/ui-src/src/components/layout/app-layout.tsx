import { Outlet } from "react-router-dom";

import { Sidebar } from "@/components/layout/sidebar";
import { TopBar } from "@/components/layout/top-bar";

/**
 * 应用骨架:左侧导航(五屏)+ 顶栏(品类/全局状态)+ 内容区。
 * 暗色单主题;滚动只发生在内容区,侧栏/顶栏常驻。
 */
export function AppLayout() {
  return (
    <div className="flex h-full overflow-hidden bg-background text-foreground">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
