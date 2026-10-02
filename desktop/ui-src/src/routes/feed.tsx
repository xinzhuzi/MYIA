import { Bookmark, Inbox, Star } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

/**
 * 情报流(骨架):Miniflux 式信息架构 —— 未读/星标/稍后读过滤 + 卡片瀑布。
 * 条目数据(api.storeItems)与已读/星标交互 C 阶段接入。
 */
export function FeedPage() {
  return (
    <div className="flex flex-col gap-4 pb-6">
      <PageHeader
        title="情报流"
        description="卡片瀑布:未读 / 星标 / 稍后读(Miniflux 式信息架构)"
      />

      <div className="flex items-center gap-1 px-6">
        <Button variant="secondary" size="sm" disabled>
          <Inbox className="size-3.5" />
          未读
        </Button>
        <Button variant="ghost" size="sm" disabled>
          <Star className="size-3.5" />
          星标
        </Button>
        <Button variant="ghost" size="sm" disabled>
          <Bookmark className="size-3.5" />
          稍后读
        </Button>
        <span className="ml-2 text-[11px] text-muted-foreground">
          过滤与已读/星标状态写入待 C 阶段接入
        </span>
      </div>

      <div className="px-6">
        <Card>
          <CardContent className="p-0">
            <EmptyState
              tag="C 阶段接入"
              title="情报流还是空的"
              description="跑一次采集后,条目将按新→旧排列在此;数据源为 store.items(新→旧)"
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
