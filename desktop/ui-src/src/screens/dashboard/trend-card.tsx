import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, SidecarRequestError } from "@/lib/api";
import type { TrendDay as TrendDayRow } from "@/lib/api";

import {
  fillDailyCounts,
  TREND_WINDOW_DAYS,
  TREND_WINDOW_DEFAULT,
  trendCounts,
  toSparklinePoints,
  utcToday,
} from "./api";
import type { TrendWindowDays } from "./api";

/** sparkline 画布(viewBox;手写 SVG polyline,零新依赖) */
const SPARK_WIDTH = 260;
const SPARK_HEIGHT = 48;

/**
 * 采集量趋势卡(B4,10-03-v112-desktop-parity):items 按 first_seen UTC 逐日
 * 计数(store.trend → fillDailyCounts 补零 → SVG sparkline)。窗口 7/14/30 天
 * 头部切换;口径 = UTC 逐日(卡面如实注记,不伪称本地时区)。历史 run 语境
 * 经近期 run 卡(runs.list,C3)可达,本卡只依赖 items 表。
 */
export function TrendCard() {
  const [windowDays, setWindowDays] = useState<TrendWindowDays>(TREND_WINDOW_DEFAULT);
  const [days, setDays] = useState<TrendDayRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<SidecarRequestError | null>(null);

  const refresh = useCallback(
    async (target: TrendWindowDays) => {
      setLoading(true);
      setError(null);
      try {
        const result = await api.storeTrend({ days: target });
        setDays(fillDailyCounts(result.days, target, utcToday()));
      } catch (err) {
        setError(
          err instanceof SidecarRequestError
            ? err
            : new SidecarRequestError({ code: "transport_error", path: "$", message: String(err) }),
        );
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void refresh(windowDays);
  }, [refresh, windowDays]);

  const counts = days ? trendCounts(days) : [];
  const total = counts.reduce((sum, count) => sum + count, 0);
  const peak = days ? counts.reduce((max, count) => Math.max(max, count), 0) : 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">采集量趋势</CardTitle>
        <CardDescription>
          每日入库条目数(UTC 逐日;窗口切换即时重查)
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <div className="flex items-center gap-1">
          {TREND_WINDOW_DAYS.map((option) => (
            <Button
              key={option}
              variant={windowDays === option ? "secondary" : "ghost"}
              size="sm"
              className="h-6 px-2 text-2xs"
              aria-pressed={windowDays === option}
              aria-label={`趋势窗口 ${option} 天`}
              onClick={() => setWindowDays(option)}
            >
              {option}天
            </Button>
          ))}
          {loading ? <Loader2 className="size-3 animate-spin text-muted-foreground" /> : null}
        </div>
        {loading && days === null ? (
          <>
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-3 w-2/3" />
          </>
        ) : error ? (
          <p className="text-xs text-destructive" data-testid="trend-error">
            趋势不可用({error.code}):{error.message}
          </p>
        ) : (
          <>
            <svg
              data-testid="trend-sparkline"
              viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
              className="h-12 w-full"
              role="img"
              aria-label={`近 ${windowDays} 天采集量 sparkline,共 ${total} 条,峰值 ${peak} 条`}
            >
              <polyline
                points={toSparklinePoints(counts, SPARK_WIDTH, SPARK_HEIGHT)}
                fill="none"
                strokeWidth="1.5"
                className="stroke-primary"
                vectorEffect="non-scaling-stroke"
              />
            </svg>
            <p className="flex flex-wrap items-center gap-1.5 text-2xs text-muted-foreground">
              <span data-testid="trend-total">
                近 {windowDays} 天共 {total} 条 · 峰值 {peak} 条/日
              </span>
              <Badge variant="outline">UTC 逐日</Badge>
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
