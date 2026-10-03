import { useCallback, useState } from "react";
import { Outlet } from "react-router-dom";

import { Sidebar } from "@/components/layout/sidebar";
import { TopBar } from "@/components/layout/top-bar";

/**
 * 应用骨架:左侧导航(七屏,Linear 式分组+底部 sidecar 状态区)+
 * 顶栏(面包屑/品类/全局跑一次/全局命令位留白)+ 内容区。
 * 暗色单主题;滚动只发生在内容区,侧栏/顶栏常驻。
 *
 * 品类全局选择器(C8,10-03-feed-ux):选中值提升到本层(不引状态库,
 * Outlet context 即 router 原生的 prop drilling 通道)→ 情报流屏服务端
 * category 过滤;「全部品类」= null(不传参)。
 */
export interface CategoryFilterContext {
  /** 顶栏选中的品类;null = 全部品类 */
  category: string | null;
}

export function AppLayout() {
  const [category, setCategory] = useState<string | null>(null);
  const handleCategoryChange = useCallback((next: string | null) => setCategory(next), []);
  return (
    <div className="flex h-full overflow-hidden bg-background text-foreground">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar category={category} onCategoryChange={handleCategoryChange} />
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet context={{ category } satisfies CategoryFilterContext} />
        </main>
      </div>
    </div>
  );
}
