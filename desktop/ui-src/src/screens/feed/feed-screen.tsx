import { useCallback, useEffect, useMemo, useState } from "react";
import { Bookmark, Inbox, Play, RefreshCw, Star } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, onSidecarEvent, SidecarRequestError } from "@/lib/api";
import type { FeedItem, UnlistenFn } from "@/lib/api";

import {
  applyFeedFilter,
  appendFeedPage,
  fetchFeedPage,
  formatRelativeTime,
  itemKey,
  loadFeedStates,
  primaryScore,
  saveFeedStates,
  toggleMarker,
} from "./api";
import type { FeedFilter, FeedStateMap } from "./api";

/** 过滤页签(默认未读,Miniflux 式) */
const FILTERS: { key: FeedFilter; label: string }[] = [
  { key: "unread", label: "未读" },
  { key: "starred", label: "星标" },
  { key: "later", label: "稍后读" },
  { key: "all", label: "全部" },
];

const EMPTY_TEXT: Record<FeedFilter, { title: string; description: string }> = {
  unread: { title: "没有未读条目", description: "新采集的条目会按新→旧出现在这里" },
  starred: { title: "还没有星标", description: "点击条目卡上的星形按钮收藏重要情报" },
  later: { title: "稍后读还是空的", description: "点击书签按钮把条目放入稍后读" },
  all: { title: "情报流还是空的", description: "数据源为 store.items(新→旧);先跑一次采集" },
};

/** 空流 CTA「运行第一个插件」的状态机(idle → starting → collecting → done/error) */
type RunCtaState =
  | { phase: "idle" }
  | { phase: "starting" }
  | { phase: "collecting"; runId: number }
  | { phase: "done" }
  | { phase: "error"; message: string };

function FeedCard({
  item,
  state,
  onMarkRead,
  onToggle,
}: {
  item: FeedItem;
  state: { read?: boolean; starred?: boolean; later?: boolean };
  onMarkRead: (item: FeedItem) => void;
  onToggle: (item: FeedItem, marker: "starred" | "later" | "read") => void;
}) {
  const score = primaryScore(item);
  const time = formatRelativeTime(item.first_seen);
  return (
    <div
      data-testid={`feed-item-${item.id ?? itemKey(item)}`}
      className={`rounded-md border px-3 py-2.5 transition-colors ${
        state.read ? "border-border/50 bg-muted/20" : "border-border bg-card"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <button
          type="button"
          className="min-w-0 text-left text-sm font-medium text-foreground hover:text-primary"
          onClick={() => onMarkRead(item)}
          title={item.url}
        >
          {!state.read ? <span className="mr-1.5 inline-block size-1.5 rounded-full bg-primary align-middle" /> : null}
          {item.title || item.url}
        </button>
        <div className="flex shrink-0 items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon"
            className="size-6"
            aria-pressed={state.starred === true}
            aria-label="星标"
            onClick={() => onToggle(item, "starred")}
          >
            <Star className={state.starred ? "size-3.5 fill-warning text-warning" : "size-3.5 text-muted-foreground"} />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-6"
            aria-pressed={state.later === true}
            aria-label="稍后读"
            onClick={() => onToggle(item, "later")}
          >
            <Bookmark className={state.later ? "size-3.5 fill-primary text-primary" : "size-3.5 text-muted-foreground"} />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-6"
            aria-pressed={state.read === true}
            aria-label={state.read ? "标记未读" : "标记已读"}
            onClick={() => onToggle(item, "read")}
          >
            <Inbox className="size-3.5 text-muted-foreground" />
          </Button>
        </div>
      </div>

      <div className="mt-1 flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] text-muted-foreground">
          {item.source ?? "未知来源"} · {time}
        </span>
        {item.category ? <Badge variant="secondary">{item.category}</Badge> : null}
        {item.tags.slice(0, 4).map((tag) => (
          <Badge key={tag} variant="outline">
            {tag}
          </Badge>
        ))}
        {score !== null ? (
          <Badge variant="default" title="精评分数(维度最高分)">
            {score.toFixed(2)}
          </Badge>
        ) : null}
      </div>

      {item.content ? (
        <p className="mt-1.5 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{item.content}</p>
      ) : null}
    </div>
  );
}

/**
 * 情报流:条目卡片瀑布 + 未读/星标/稍后读三态(本地态,localStorage 持久)
 * + 游标分页加载(见 ./api 的协议缺口注记)。
 */
export function FeedScreen() {
  const navigate = useNavigate();
  const [items, setItems] = useState<FeedItem[]>([]);
  const [states, setStates] = useState<FeedStateMap>({});
  const [filter, setFilter] = useState<FeedFilter>("unread");
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<SidecarRequestError | null>(null);
  /** health.first_run:空流时区分「无插件(首跑初始化)」与「有插件未采集」 */
  const [firstRun, setFirstRun] = useState(false);
  const [cta, setCta] = useState<RunCtaState>({ phase: "idle" });

  useEffect(() => {
    setStates(loadFeedStates());
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await fetchFeedPage({ cursor: null });
      setItems(page.items);
      setCursor(page.nextCursor);
      setHasMore(page.hasMore);
      if (page.items.length === 0) {
        // 空流才追问 health(一次 RPC):空态文案按有无插件分叉
        try {
          setFirstRun((await api.health()).first_run ?? false);
        } catch {
          // health 失败不遮蔽情报流自身的空态;CTA 点击时还有一次兜底
        }
      }
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

  const loadMore = useCallback(async () => {
    if (loadingMore || cursor === null) return;
    setLoadingMore(true);
    try {
      const page = await fetchFeedPage({ cursor });
      setCursor(page.nextCursor);
      setItems((current) => {
        const merged = appendFeedPage(current, page);
        // 追加 0 条 = 游标停滞(同刻批量超过页大小),判停防死循环(见 ./api 注记)
        setHasMore(page.hasMore && merged.added > 0);
        return merged.items;
      });
    } catch (err) {
      setError(
        err instanceof SidecarRequestError
          ? err
          : new SidecarRequestError({ code: "transport_error", path: "$", message: String(err) }),
      );
    } finally {
      setLoadingMore(false);
    }
  }, [cursor, loadingMore]);

  const updateStates = useCallback((next: FeedStateMap) => {
    setStates(next);
    saveFeedStates(next);
  }, []);

  const markRead = useCallback(
    (item: FeedItem) => {
      const key = itemKey(item);
      if (states[key]?.read) return;
      updateStates({ ...states, [key]: { ...(states[key] ?? {}), read: true } });
    },
    [states, updateStates],
  );

  const toggle = useCallback(
    (item: FeedItem, marker: "starred" | "later" | "read") => {
      updateStates(toggleMarker(states, itemKey(item), marker));
    },
    [states, updateStates],
  );

  const visible = useMemo(() => applyFeedFilter(items, states, filter), [items, states, filter]);

  /** 空流 CTA:health 取第一个可加载插件 → run.start(yaml 绝对路径,与 sources.write 同口径) */
  const startFirstPlugin = useCallback(async () => {
    setCta({ phase: "starting" });
    try {
      const health = await api.health();
      const plugin = health.plugins.find((candidate) => candidate.loaded) ?? health.plugins[0];
      if (!plugin) {
        setCta({
          phase: "error",
          message: "插件目录为空:重启应用触发首跑初始化,或到「源管理」检查插件目录。",
        });
        return;
      }
      const started = await api.runStart({ yaml: plugin.file });
      setCta({ phase: "collecting", runId: started.run_id });
    } catch (err) {
      setCta({
        phase: "error",
        message: err instanceof SidecarRequestError ? `${err.code}:${err.message}` : String(err),
      });
    }
  }, []);

  // completed 事件 → 回 idle(可再跑)+ 自动刷新;订阅随 collecting 状态起止
  useEffect(() => {
    if (cta.phase !== "collecting") return;
    let unlisten: UnlistenFn | null = null;
    let cancelled = false;
    void onSidecarEvent((event) => {
      if (event.type === "completed" && event.run_id === cta.runId) {
        setCta({ phase: "done" });
        void refresh();
      }
    }).then((un) => {
      if (cancelled) un();
      else unlisten = un;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [cta, refresh]);

  return (
    <div className="flex flex-col gap-4 pb-6">
      <PageHeader
        title="情报流"
        description="卡片瀑布:未读 / 星标 / 稍后读(本地态,随浏览器存储持久)"
        actions={
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw className={loading ? "size-3.5 animate-spin" : "size-3.5"} />
            刷新
          </Button>
        }
      />

      <div className="flex items-center gap-1 px-6">
        {FILTERS.map((entry) => (
          <Button
            key={entry.key}
            variant={filter === entry.key ? "secondary" : "ghost"}
            size="sm"
            aria-label={`过滤:${entry.label}`}
            aria-pressed={filter === entry.key}
            onClick={() => setFilter(entry.key)}
          >
            {entry.label}
          </Button>
        ))}
        <span className="ml-2 text-[11px] text-muted-foreground">
          {filter === "all" ? `共 ${items.length} 条` : `${visible.length} / ${items.length} 条`}
        </span>
      </div>

      <div className="flex flex-col gap-2 px-6">
        {error ? (
          <Card data-testid="feed-error">
            <CardContent className="pt-1">
              <p className="text-sm font-medium text-destructive">
                情报流不可用(sidecar 错误码 {error.code})
              </p>
              <p className="mt-1 text-xs text-muted-foreground">{error.message}</p>
            </CardContent>
          </Card>
        ) : null}

        {loading && items.length === 0 ? (
          <>
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </>
        ) : visible.length === 0 ? (
          items.length === 0 && firstRun ? (
            <Card data-testid="feed-first-run">
              <CardContent className="p-0">
                <EmptyState
                  title="还没有可运行的插件"
                  description="应用首次运行尚未装上官方插件;重启应用会自动完成初始化,或到「源管理」查看插件目录。"
                  tag="首跑"
                  action={
                    <Button variant="outline" size="sm" onClick={() => navigate("/sources")}>
                      去源管理
                    </Button>
                  }
                />
              </CardContent>
            </Card>
          ) : items.length === 0 ? (
            <Card data-testid="feed-run-cta">
              <CardContent className="p-0">
                <EmptyState
                  title="情报流还是空的"
                  description={
                    cta.phase === "collecting"
                      ? `采集中(run #${cta.runId}),完成后自动刷新…`
                      : cta.phase === "done"
                        ? "本次采集已结束;若仍无条目,可到「日志」查看运行明细。"
                        : cta.phase === "error"
                          ? cta.message
                          : "先运行一个插件:采集到的条目会按新→旧出现在这里。"
                  }
                  action={
                    cta.phase === "collecting" ? (
                      <Button size="sm" disabled>
                        <RefreshCw className="size-3.5 animate-spin" />
                        采集中…
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        onClick={() => void startFirstPlugin()}
                        disabled={cta.phase === "starting"}
                      >
                        <Play className="size-3.5" />
                        {cta.phase === "starting" ? "启动中…" : "运行第一个插件"}
                      </Button>
                    )
                  }
                />
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardContent className="p-0">
                <EmptyState title={EMPTY_TEXT[filter].title} description={EMPTY_TEXT[filter].description} />
              </CardContent>
            </Card>
          )
        ) : (
          visible.map((item) => (
            <FeedCard
              key={itemKey(item)}
              item={item}
              state={states[itemKey(item)] ?? {}}
              onMarkRead={markRead}
              onToggle={toggle}
            />
          ))
        )}

        {hasMore && !loading ? (
          <Button variant="outline" size="sm" className="self-center" onClick={() => void loadMore()} disabled={loadingMore}>
            {loadingMore ? "加载中…" : "加载更早的条目"}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
