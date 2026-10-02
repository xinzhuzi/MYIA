import { CircleDot, HeartPulse, TrendingUp } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * 仪表盘(骨架):品类状态 / 源健康度 / 采集量趋势三块信息架构先立,
 * 数据面(run 历史/健康度聚合)C 阶段经 api.health()/run.* 接入。
 */
export function DashboardPage() {
  return (
    <div className="flex flex-col gap-4 pb-6">
      <PageHeader
        title="仪表盘"
        description="品类运行状态、源健康度(ok / degraded / dead)与采集量趋势的总览"
      />

      <div className="grid grid-cols-1 gap-4 px-6 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CircleDot className="size-3.5 text-muted-foreground" />
              品类状态
            </CardTitle>
            <CardDescription>已装品类、调度与最近 run 结果</CardDescription>
          </CardHeader>
          <CardContent>
            <EmptyState
              compact
              tag="C 阶段接入"
              title="暂无品类数据"
              description="将聚合 api.health() 的插件清单与最近 run 状态"
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <HeartPulse className="size-3.5 text-muted-foreground" />
              源健康度
            </CardTitle>
            <CardDescription>ok / degraded / dead / unknown 四态分布</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <Badge variant="ok">ok</Badge>
              <Skeleton className="h-3 w-14" />
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="warning">degraded</Badge>
              <Skeleton className="h-3 w-14" />
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="destructive">dead</Badge>
              <Skeleton className="h-3 w-14" />
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              分布计数待 C 阶段自 health.summary 渲染
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <TrendingUp className="size-3.5 text-muted-foreground" />
              采集量趋势
            </CardTitle>
            <CardDescription>按日条目数(run 历史)折线</CardDescription>
          </CardHeader>
          <CardContent>
            <Skeleton className="h-20 w-full" />
            <p className="mt-2 text-[11px] text-muted-foreground">
              趋势序列待 C 阶段自 run 记录聚合
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
