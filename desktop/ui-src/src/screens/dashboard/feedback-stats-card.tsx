import { useCallback, useEffect, useState } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, SidecarRequestError } from "@/lib/api";
import type { FeedbackStatsResult } from "@/lib/api";

/** 统计窗口(与 CLI feedback stats 缺省同门) */
const WINDOW_DAYS = 14;

/**
 * 反馈统计卡(B2,10-03-v112-desktop-parity):feedback.stats 的桌面可见面——
 * 好/坏计数 + 负反馈 Top 类目摘要(调参线索)。卡片 👍/👎 的标记入口在
 * 情报流卡片(feed-card-feedback),本卡只做统计呈现(design §5:把 B2 的
 * UI 足迹压到「feed 卡片一个小组件 + dashboard 一张卡」)。
 */
export function FeedbackStatsCard() {
  const [stats, setStats] = useState<FeedbackStatsResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<SidecarRequestError | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setStats(await api.feedbackStats({ window_days: WINDOW_DAYS }));
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

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">反馈统计</CardTitle>
        <CardDescription>近 {WINDOW_DAYS} 天 👍/👎 计数与负反馈类目(channel=desktop 与 CLI 同库)</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {loading && stats === null ? (
          <>
            <Skeleton className="h-8 w-24" />
            <Skeleton className="h-3 w-3/4" />
          </>
        ) : error ? (
          <p className="text-xs text-destructive" data-testid="feedback-stats-error">
            反馈统计不可用({error.code}):{error.message}
          </p>
        ) : stats === null ? null : stats.stats.total === 0 ? (
          <p className="py-2 text-xs text-muted-foreground" data-testid="feedback-stats-empty">
            还没有反馈;在情报流卡片点 👍/👎,统计与调参从这里可见。
          </p>
        ) : (
          <>
            <div className="flex items-baseline gap-3">
              <span className="flex items-center gap-1 text-sm text-foreground">
                <ThumbsUp className="size-3.5 text-ok" />
                <span data-testid="feedback-good" className="font-semibold">
                  {stats.stats.good}
                </span>
                好
              </span>
              <span className="flex items-center gap-1 text-sm text-foreground">
                <ThumbsDown className="size-3.5 text-destructive" />
                <span data-testid="feedback-bad" className="font-semibold">
                  {stats.stats.bad}
                </span>
                坏
              </span>
              <span className="text-2xs text-muted-foreground">
                共 {stats.stats.total} 条 · 差评率 {(stats.stats.bad_ratio * 100).toFixed(0)}%
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-2xs text-muted-foreground">负反馈 Top 类目:</span>
              {stats.stats.top_bad_categories.length === 0 ? (
                <span className="text-2xs text-muted-foreground">无</span>
              ) : (
                stats.stats.top_bad_categories.map((entry) => (
                  <Badge key={entry.key} variant="warning">
                    {entry.key} ×{entry.bad}
                  </Badge>
                ))
              )}
            </div>
            <p className="text-2xs text-muted-foreground">
              负反馈将在下轮维护阶段参与调参(Top 类目/词降权);enrich 关闭时仅入库不生效。
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
