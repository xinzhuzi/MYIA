import { useCallback, useEffect, useState } from "react";
import { Activity, CircleDot, HeartPulse, Loader2, Play, RefreshCw, TrendingUp } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { api, onSidecarEvent, SidecarRequestError } from "@/lib/api";
import type { SourceHealthState, UnlistenFn } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  buildCategoryCards,
  buildOverviewStats,
  buildSourceHealthCards,
  fetchTrendWindow,
  formatDuration,
  formatRelativeTime,
  formatSuccessRate,
  loadDashboardData,
  runItemCount,
  summarizeRuns,
  summarizeSourceHealth,
  TREND_WINDOW_DAYS,
  TREND_WINDOW_DEFAULT,
  trendCounts,
  utcToday,
} from "./api";
import type {
  CategoryCardModel,
  CategoryTone,
  DashboardData,
  DashboardRun,
  OverviewStats,
  RunSuccessSummary,
  SourceHealthCardModel,
  SourceHealthCounts,
  TrendDay,
  TrendWindowDays,
} from "./api";
import { FeedbackStatsCard } from "./feedback-stats-card";
import { Sparkline } from "./sparkline";

/** 品类 tone → 徽标(健康度四态语义沿用共享 Badge:ok/warning/destructive) */
const TONE_BADGE: Record<CategoryTone, { variant: "ok" | "warning" | "destructive"; label: string }> = {
  ok: { variant: "ok", label: "正常" },
  warning: { variant: "warning", label: "降级" },
  dead: { variant: "destructive", label: "异常" },
};

/**
 * 状态点(teardown-vercel-dashboard #3:8px 圆点+13px 标签,四色映射
 * Ready/Error/Building/Queued → ok/dead/warning/unknown;色走 D2 语义 token
 * bg-ok/warning/dead/unknown,与源管理 HealthBadge 同一色源)。
 */
const SOURCE_STATE: Record<SourceHealthState, { dot: string; label: string }> = {
  ok: { dot: "bg-ok", label: "正常" },
  degraded: { dot: "bg-warning", label: "退化" },
  dead: { dot: "bg-dead", label: "失效" },
  unknown: { dot: "bg-unknown", label: "未知" },
};

function StatusDot({ state, reason }: { state: SourceHealthState; reason?: string }) {
  const health = SOURCE_STATE[state];
  return (
    <span
      data-health={state}
      title={reason}
      className="inline-flex items-center gap-1.5 text-sm text-foreground"
    >
      <span aria-hidden className={cn("size-2 shrink-0 rounded-full", health.dot)} />
      {health.label}
    </span>
  );
}

/** run 状态 → 徽标(runs 表 status 语义;active = 当前会话进行中,C3) */
function runStatusBadge(run: DashboardRun) {
  if (run.active) {
    return { variant: "default" as const, label: "运行中" };
  }
  switch (run.status) {
    case "success":
      return { variant: "ok" as const, label: "成功" };
    case "partial":
      return { variant: "warning" as const, label: "部分" };
    case "config_error":
      return { variant: "warning" as const, label: "配置" };
    case "failed":
      return { variant: "destructive" as const, label: "失败" };
    case "cancelled":
      return { variant: "unknown" as const, label: "已取消" };
    case "running":
      // 表内 running 且无内存活跃 = sidecar 中断遗留的僵尸行(如实标注)
      return { variant: "warning" as const, label: "中断" };
    default:
      return { variant: "unknown" as const, label: run.status ?? "未知" };
  }
}

/** 「跑一次」状态机(G4,10-03-feed-ux;照抄 feed 空态 CTA 形状) */
type RunOnceState =
  | { phase: "idle" }
  | { phase: "starting" }
  | { phase: "collecting"; runId: number }
  | { phase: "done" }
  | { phase: "error"; message: string };

function CategoryCard({
  category,
  onRunFinished,
}: {
  category: CategoryCardModel;
  /** completed 后回调(仪表盘刷新 run 历史与品类状态) */
  onRunFinished: () => void;
}) {
  const tone = TONE_BADGE[category.tone];
  const [runOnce, setRunOnce] = useState<RunOnceState>({ phase: "idle" });

  const startRun = useCallback(async () => {
    setRunOnce({ phase: "starting" });
    try {
      const started = await api.runStart({ yaml: category.file });
      setRunOnce({ phase: "collecting", runId: started.run_id });
    } catch (err) {
      setRunOnce({
        phase: "error",
        message: err instanceof SidecarRequestError ? `${err.code}: ${err.message}` : String(err),
      });
    }
  }, [category.file]);

  // completed 事件 → done + 仪表盘刷新;订阅随 collecting 状态起止(同 feed CTA)
  useEffect(() => {
    if (runOnce.phase !== "collecting") return;
    let unlisten: UnlistenFn | null = null;
    let cancelled = false;
    void onSidecarEvent((event) => {
      if (event.type === "completed" && event.run_id === runOnce.runId) {
        setRunOnce({ phase: "done" });
        onRunFinished();
      }
    }).then((un) => {
      if (cancelled) un();
      else unlisten = un;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [runOnce, onRunFinished]);

  const busy = runOnce.phase === "starting" || runOnce.phase === "collecting";
  return (
    <div
      data-testid={`category-${category.file}`}
      className="flex items-center justify-between gap-2 rounded-md border border-border/60 bg-muted/40 px-2.5 py-2"
    >
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="truncate text-xs font-medium text-foreground">{category.name}</p>
        <p className="text-2xs text-muted-foreground">
          {category.sourceCount} 源
          {category.schedule ? ` · ${category.schedule}` : " · 手动"}
          {category.nextFireAt ? " · 有排程" : ""}
        </p>
        {runOnce.phase === "error" ? (
          <p className="text-2xs text-destructive" data-testid={`run-once-error-${category.file}`}>
            跑一次失败:{runOnce.message}
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {!category.loaded ? <Badge variant="unknown">未载入</Badge> : null}
        <Badge variant={tone.variant}>{tone.label}</Badge>
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          aria-label={`跑一次:${category.name}`}
          title="手动触发该品类采集一次(run.start)"
          disabled={busy}
          onClick={() => void startRun()}
        >
          {runOnce.phase === "collecting" ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Play className="size-3.5" />
          )}
        </Button>
      </div>
    </div>
  );
}

function RecentRunRow({ run }: { run: DashboardRun }) {
  const badge = runStatusBadge(run);
  const itemCount = runItemCount(run);
  return (
    <div
      data-testid={`recent-run-${run.runId}`}
      className="flex items-center justify-between gap-2 border-b border-border/40 py-1.5 last:border-b-0"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="font-mono text-2xs text-muted-foreground">#{run.runId}</span>
        <span className="truncate text-xs text-foreground">{run.category}</span>
        {run.dry ? <Badge variant="outline">dry</Badge> : null}
      </div>
      <div className="flex shrink-0 items-center gap-2 text-2xs text-muted-foreground">
        {itemCount !== null ? <span>{itemCount} 条</span> : null}
        <span className="font-mono">{formatDuration(run.durationMs)}</span>
        <Badge variant={badge.variant}>{badge.label}</Badge>
      </div>
    </div>
  );
}

/**
 * 概览条格(teardown-vercel-dashboard #2:小标签 = 大写+弱色,大数字 = tnum
 * 全局已开;value=null 显 — 不虚构)。note = 弱注记(口径说明)。
 */
function StatCell({
  label,
  value,
  note,
  destructive = false,
  testid,
}: {
  label: string;
  value: number | string | null;
  note: string;
  destructive?: boolean;
  testid: string;
}) {
  return (
    <div data-testid={testid} className="flex flex-col gap-1 md:px-6 md:first:pl-0">
      <p className="text-2xs font-medium uppercase tracking-wider text-muted-foreground">{label}</p>
      <p
        className={cn(
          "text-2xl font-semibold tabular-nums",
          destructive ? "text-destructive" : "text-foreground",
        )}
      >
        {value === null ? "—" : value}
      </p>
      <p className="text-2xs text-muted-foreground">{note}</p>
    </div>
  );
}

/**
 * 源健康卡(teardown-vercel-dashboard #4:名称 14 medium + muted 次行 + 相对
 * 时间 muted 右置;状态点居首行左 = Vercel 项目卡「状态点+词 → 名称 → 次行
 * → 相对时间」层级)。
 */
function SourceCard({ card }: { card: SourceHealthCardModel }) {
  return (
    <div
      data-testid={`source-card-${card.key}`}
      className="flex flex-col gap-2 rounded-lg border border-border/60 bg-card p-4"
    >
      <div className="flex items-start justify-between gap-2">
        <StatusDot state={card.state} reason={card.reason} />
        <span className="font-mono text-xs text-muted-foreground">
          {formatRelativeTime(card.lastObservedAt)}
        </span>
      </div>
      <p className="truncate text-base font-medium text-foreground" title={`${card.name} · ${card.engine}`}>
        {card.name}
      </p>
      <p className="truncate text-xs text-muted-foreground">
        {card.pluginName}
        {card.latestItemCount !== null ? ` · 最近 ${card.latestItemCount} 条` : ""}
      </p>
    </div>
  );
}

/**
 * 仪表盘(结构性重做,10-03-ui-deep-imitation D4;对标 teardown-vercel-dashboard):
 * 概览条(今日采集/活跃源/推送成功/告警)→ 采集量趋势(Select 时间范围 + 自绘
 * SVG sparkline,D5 决议⑥零依赖;活跃 run 时末点呼吸)+ 品类状态 → 源健康度
 * 卡网格(StatusDot 四色 + 相对时间,gap-6)→ 近期 run 成功率 + 反馈统计。
 * 数据 = doctor + runs.list + run.status + store.trend(见 ./api;C3:重启 .app
 * 后历史 run 仍可达)。加载/错误/空态三态齐备;趋势独立降级不拖垮整屏。
 */
export function DashboardScreen() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<SidecarRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [windowDays, setWindowDays] = useState<TrendWindowDays>(TREND_WINDOW_DEFAULT);
  const [trend, setTrend] = useState<TrendDay[] | null>(null);
  const [trendError, setTrendError] = useState<SidecarRequestError | null>(null);
  const [trendLoading, setTrendLoading] = useState(true);

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

  const refreshTrend = useCallback(async (days: TrendWindowDays) => {
    setTrendLoading(true);
    setTrendError(null);
    try {
      setTrend(await fetchTrendWindow(days));
    } catch (err) {
      setTrendError(
        err instanceof SidecarRequestError
          ? err
          : new SidecarRequestError({ code: "transport_error", path: "$", message: String(err) }),
      );
    } finally {
      setTrendLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    void refreshTrend(windowDays);
  }, [refreshTrend, windowDays]);

  const healthCounts: SourceHealthCounts | null = data ? summarizeSourceHealth(data.doctor) : null;
  const runSummary: RunSuccessSummary | null = data ? summarizeRuns(data.runs) : null;
  const categories: CategoryCardModel[] = data ? buildCategoryCards(data.doctor) : [];
  const sourceCards: SourceHealthCardModel[] = data ? buildSourceHealthCards(data.doctor, data.runs) : [];
  const overview: OverviewStats | null = data
    ? buildOverviewStats(data.doctor, data.runs, trend, utcToday())
    : null;

  const counts = trend ? trendCounts(trend) : [];
  const trendTotal = counts.reduce((sum, count) => sum + count, 0);
  const trendPeak = counts.reduce((max, count) => Math.max(max, count), 0);
  const collecting = runSummary !== null && runSummary.running > 0;

  return (
    <div data-testid="dashboard-screen-root" className="flex flex-col gap-6 pb-6">
      <PageHeader
        title="仪表盘"
        description="概览条 / 采集量趋势 / 源健康度 / 品类与近期 run"
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void refresh();
              void refreshTrend(windowDays);
            }}
            disabled={loading}
          >
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

      {/* 概览条(D4;teardown #2:一行四格,大写小标签 + 大数字 tnum;趋势不可达时今日格如实显 —) */}
      <section data-testid="dashboard-overview" aria-label="今日概览" className="px-6">
        <Card>
          <CardContent className="py-5">
            <div className="grid grid-cols-2 gap-x-6 gap-y-4 md:grid-cols-4 md:gap-x-0 md:divide-x md:divide-border/60">
              {loading && overview === null ? (
                [0, 1, 2, 3].map((index) => (
                  <div key={index} className="flex flex-col gap-2 md:px-6 md:first:pl-0">
                    <Skeleton className="h-3 w-16" />
                    <Skeleton className="h-8 w-12" />
                  </div>
                ))
              ) : overview === null ? null : (
                <>
                  <StatCell
                    testid="stat-today-items"
                    label="今日采集"
                    value={overview.todayItems}
                    note="UTC 日口径 · items 入库"
                  />
                  <StatCell
                    testid="stat-active-sources"
                    label="活跃源"
                    value={overview.activeSources}
                    note={`共 ${overview.totalSources} 源 · ok+degraded`}
                  />
                  <StatCell
                    testid="stat-push-success"
                    label="推送成功"
                    value={overview.pushOkToday}
                    note="今日(UTC)run 的 ok 推送"
                  />
                  <StatCell
                    testid="stat-alerts"
                    label="告警"
                    value={overview.alerts}
                    note="doctor error+warning 发现"
                    destructive={overview.alerts > 0}
                  />
                </>
              )}
            </div>
          </CardContent>
        </Card>
      </section>

      <div className="grid grid-cols-1 gap-6 px-6 md:grid-cols-3">
        {/* 采集量趋势(teardown #6:Select 时间范围 + 自绘 sparkline;building 态末点呼吸) */}
        <Card className="md:col-span-2">
          <CardHeader>
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="flex items-center gap-2">
                <TrendingUp className="size-3.5 text-muted-foreground" />
                采集量趋势
                {collecting ? (
                  <Badge variant="default" className="animate-pulse">
                    采集中
                  </Badge>
                ) : null}
              </CardTitle>
              <Select
                value={String(windowDays)}
                onValueChange={(value) => setWindowDays(Number(value) as TrendWindowDays)}
              >
                <SelectTrigger size="sm" className="h-6 text-xs" aria-label="趋势时间范围">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TREND_WINDOW_DAYS.map((option) => (
                    <SelectItem key={option} value={String(option)}>
                      {option} 天
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <CardDescription>每日入库条目数(UTC 逐日;时间范围切换即时重查)</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {trendLoading && trend === null ? (
              <>
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-3 w-2/3" />
              </>
            ) : trendError ? (
              <p className="text-xs text-destructive" data-testid="dashboard-trend-error">
                趋势不可用({trendError.code}):{trendError.message}
              </p>
            ) : (
              <>
                <Sparkline
                  values={counts}
                  data-testid="dashboard-sparkline"
                  pulse={collecting}
                  aria-label={`近 ${windowDays} 天采集量 sparkline,共 ${trendTotal} 条,峰值 ${trendPeak} 条`}
                />
                <p className="flex flex-wrap items-center gap-1.5 text-2xs text-muted-foreground">
                  <span data-testid="trend-total">
                    近 {windowDays} 天共 {trendTotal} 条 · 峰值 {trendPeak} 条/日
                  </span>
                  <Badge variant="outline">UTC 逐日</Badge>
                </p>
              </>
            )}
          </CardContent>
        </Card>

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
                description="首次启动会自动装载随包官方品类;若仍未出现,重启应用重试初始化,或到「源管理」查看插件目录"
              />
            ) : (
              categories.map((category) => (
                <CategoryCard key={category.file} category={category} onRunFinished={() => void refresh()} />
              ))
            )}
          </CardContent>
        </Card>
      </div>

      {/* 源健康度卡网格(D4;teardown #3/#4:四态点 + 14 medium 名称 + muted 次行 + 相对时间;gap-6) */}
      <section aria-label="源健康度" className="flex flex-col gap-3 px-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <HeartPulse className="size-3.5 text-muted-foreground" />
            <h2 className="text-sm font-medium text-foreground">源健康度</h2>
            <span className="text-xs text-muted-foreground">
              {sourceCards.length} 个源 · 坏者(dead → degraded → unknown)靠前
            </span>
          </div>
          {healthCounts === null ? null : (
            <div className="flex items-center gap-3">
              {(Object.keys(SOURCE_STATE) as SourceHealthState[]).map((state) => (
                <span
                  key={state}
                  data-testid={`health-${state}`}
                  title={`${SOURCE_STATE[state].label} ${healthCounts[state]}`}
                  className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"
                >
                  <span aria-hidden className={cn("size-2 rounded-full", SOURCE_STATE[state].dot)} />
                  {healthCounts[state]}
                </span>
              ))}
            </div>
          )}
        </div>
        <div
          data-testid="source-health-grid"
          className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
        >
          {loading && !data ? (
            [0, 1, 2, 3].map((index) => (
              <Skeleton key={index} className="h-28 w-full" />
            ))
          ) : sourceCards.length === 0 ? (
            <div className="col-span-full">
              <EmptyState
                compact
                title="暂无源"
                description="已载品类下的采集源会在此按健康度展示;先到「源管理」确认品类与源配置"
              />
            </div>
          ) : (
            sourceCards.map((card) => <SourceCard key={card.key} card={card} />)
          )}
        </div>
      </section>

      <div className="grid grid-cols-1 gap-6 px-6 md:grid-cols-3">
        {/* 近期 run 成功率 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity className="size-3.5 text-muted-foreground" />
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
                  <span className="text-2xs text-muted-foreground">
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
                    runSummary.recent.map((run) => <RecentRunRow key={run.runId} run={run} />)
                  )}
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* 反馈统计(B2,10-03-v112-desktop-parity;feedback.stats 好/坏 + Top 类目) */}
        <FeedbackStatsCard />
      </div>
    </div>
  );
}
