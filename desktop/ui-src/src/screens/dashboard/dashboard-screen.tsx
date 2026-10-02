import { useCallback, useEffect, useState } from "react";
import { Activity, CircleDot, HeartPulse, RefreshCw, TrendingUp } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SidecarRequestError } from "@/lib/api";
import type { RunEntry, RunExitStatus } from "@/lib/api";

import {
  buildCategoryCards,
  formatDuration,
  formatSuccessRate,
  loadDashboardData,
  runItemCount,
  summarizeRuns,
  summarizeSourceHealth,
} from "./api";
import type { CategoryCardModel, CategoryTone, DashboardData, RunSuccessSummary, SourceHealthCounts } from "./api";

/** 品类 tone → 徽标(健康度四态语义沿用共享 Badge:ok/warning/destructive) */
const TONE_BADGE: Record<CategoryTone, { variant: "ok" | "warning" | "destructive"; label: string }> = {
  ok: { variant: "ok", label: "正常" },
  warning: { variant: "warning", label: "降级" },
  dead: { variant: "destructive", label: "异常" },
};

/** run 状态 → 徽标(退出码语义 0/1/2/3,见 types.ts RunExitStatus) */
function runStatusBadge(status: RunExitStatus | null, running: boolean) {
  if (running || status === null) {
    return { variant: "default" as const, label: "运行中" };
  }
  switch (status) {
    case "success":
      return { variant: "ok" as const, label: "成功" };
    case "partial":
      return { variant: "warning" as const, label: "部分" };
    case "config_error":
      return { variant: "warning" as const, label: "配置" };
    case "failed":
      return { variant: "destructive" as const, label: "失败" };
  }
}

function CategoryCard({ category }: { category: CategoryCardModel }) {
  const tone = TONE_BADGE[category.tone];
  return (
    <div
      data-testid={`category-${category.file}`}
      className="flex items-center justify-between gap-2 rounded-md border border-border/60 bg-muted/40 px-2.5 py-2"
    >
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="truncate text-xs font-medium text-foreground">{category.name}</p>
        <p className="text-[11px] text-muted-foreground">
          {category.sourceCount} 源
          {category.schedule ? ` · ${category.schedule}` : " · 手动"}
          {category.nextFireAt ? " · 有排程" : ""}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {!category.loaded ? <Badge variant="unknown">未载入</Badge> : null}
        <Badge variant={tone.variant}>{tone.label}</Badge>
      </div>
    </div>
  );
}

function RecentRunRow({ run }: { run: RunEntry }) {
  const running = run.state === "running";
  const badge = runStatusBadge(run.status, running);
  const itemCount = runItemCount(run);
  return (
    <div
      data-testid={`recent-run-${run.run_id}`}
      className="flex items-center justify-between gap-2 border-b border-border/40 py-1.5 last:border-b-0"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="font-mono text-[11px] text-muted-foreground">#{run.run_id}</span>
        <span className="truncate text-xs text-foreground">{run.record?.category ?? run.yaml}</span>
        {run.dry ? <Badge variant="outline">dry</Badge> : null}
      </div>
      <div className="flex shrink-0 items-center gap-2 text-[11px] text-muted-foreground">
        {itemCount !== null ? <span>{itemCount} 条</span> : null}
        <span className="font-mono">{formatDuration(run.duration_ms)}</span>
        <Badge variant={badge.variant}>{badge.label}</Badge>
      </div>
    </div>
  );
}

function HealthRow({
  label,
  count,
  variant,
}: {
  label: string;
  count: number;
  variant: "ok" | "warning" | "destructive" | "unknown";
}) {
  return (
    <div data-testid={`health-${label}`} className="flex items-center justify-between">
      <Badge variant={variant}>{label}</Badge>
      <span className="font-mono text-xs text-foreground">{count}</span>
    </div>
  );
}

/**
 * 仪表盘:品类状态卡 / 源健康度汇总 / 近期 run 成功率。
 * 数据 = doctor + run.status(见 ./api);加载/错误/空态三态齐备。
 */
export function DashboardScreen() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<SidecarRequestError | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await loadDashboardData());
    } catch (err) {
      setError(
        err instanceof SidecarRequestError
          ? err
          : new SidecarRequestError({ code: "transport_error", path: "$", message: String(err) }),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const healthCounts: SourceHealthCounts | null = data ? summarizeSourceHealth(data.doctor) : null;
  const runSummary: RunSuccessSummary | null = data ? summarizeRuns(data.runs) : null;
  const categories: CategoryCardModel[] = data ? buildCategoryCards(data.doctor) : [];

  return (
    <div data-testid="dashboard-screen-root" className="flex flex-col gap-4 pb-6">
      <PageHeader
        title="仪表盘"
        description="品类运行状态、源健康度(ok / degraded / dead)与近期 run 成功率"
        actions={
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw className={loading ? "size-3.5 animate-spin" : "size-3.5"} />
            刷新
          </Button>
        }
      />

      {error ? (
        <div className="px-6">
          <Card data-testid="dashboard-error">
            <CardContent className="pt-1">
              <p className="text-sm font-medium text-destructive">
                仪表盘数据不可用(sidecar 错误码 {error.code})
              </p>
              <p className="mt-1 text-xs text-muted-foreground">{error.message}</p>
            </CardContent>
          </Card>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 px-6 md:grid-cols-3">
        {/* 品类状态 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CircleDot className="size-3.5 text-muted-foreground" />
              品类状态
            </CardTitle>
            <CardDescription>已载品类、调度与诊断评级(0 error / N warning)</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-1.5">
            {loading && !data ? (
              <>
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </>
            ) : categories.length === 0 ? (
              <EmptyState
                compact
                title="暂无品类"
                description="doctor 未发现品类 YAML;先在插件目录放置品类配置"
              />
            ) : (
              categories.map((category) => <CategoryCard key={category.file} category={category} />)
            )}
          </CardContent>
        </Card>

        {/* 源健康度汇总 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <HeartPulse className="size-3.5 text-muted-foreground" />
              源健康度
            </CardTitle>
            <CardDescription>ok / degraded / dead / unknown 四态分布</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {loading && !data ? (
              <>
                <Skeleton className="h-3 w-3/4" />
                <Skeleton className="h-3 w-3/4" />
                <Skeleton className="h-3 w-3/4" />
              </>
            ) : healthCounts === null ? null : (
              <>
                <HealthRow label="ok" count={healthCounts.ok} variant="ok" />
                <HealthRow label="degraded" count={healthCounts.degraded} variant="warning" />
                <HealthRow label="dead" count={healthCounts.dead} variant="destructive" />
                <HealthRow label="unknown" count={healthCounts.unknown} variant="unknown" />
                <p className="mt-1 text-[11px] text-muted-foreground">
                  共 {healthCounts.ok + healthCounts.degraded + healthCounts.dead + healthCounts.unknown} 个源
                  {data?.doctor.healthy ? " · 诊断健康" : " · 存在 error 级发现,建议跑一次诊断"}
                </p>
              </>
            )}
          </CardContent>
        </Card>

        {/* 近期 run 成功率 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <TrendingUp className="size-3.5 text-muted-foreground" />
              近期 run 成功率
            </CardTitle>
            <CardDescription>最近 {runSummary?.total ?? 0} 次采集的完成与成功分布</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {loading && !data ? (
              <>
                <Skeleton className="h-8 w-24" />
                <Skeleton className="h-3 w-full" />
              </>
            ) : runSummary === null ? null : (
              <>
                <div className="flex items-baseline gap-2">
                  <span data-testid="run-success-rate" className="text-2xl font-semibold text-foreground">
                    {formatSuccessRate(runSummary.successRate)}
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    {runSummary.success}/{runSummary.finished} 次成功
                    {runSummary.running > 0 ? ` · ${runSummary.running} 个运行中` : ""}
                  </span>
                </div>
                <div className="flex flex-col">
                  {runSummary.recent.length === 0 ? (
                    <p className="flex items-center gap-1.5 py-2 text-xs text-muted-foreground">
                      <Activity className="size-3.5" />
                      还没有 run 记录;跑一次采集后这里会列出最近结果
                    </p>
                  ) : (
                    runSummary.recent.map((run) => <RecentRunRow key={run.run_id} run={run} />)
                  )}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
