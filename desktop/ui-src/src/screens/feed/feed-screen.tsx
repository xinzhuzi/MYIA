import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bookmark,
  ChevronDown,
  ChevronRight,
  Download,
  ExternalLink,
  Inbox,
  Play,
  RefreshCw,
  Search,
  Star,
} from "lucide-react";
import { useNavigate, useOutletContext } from "react-router-dom";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import type { CategoryFilterContext } from "@/components/layout/app-layout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, onSidecarEvent, SidecarRequestError } from "@/lib/api";
import type { FeedItem, UnlistenFn } from "@/lib/api";

import {
  applyFeedFilter,
  appendFeedPage,
  defaultExportName,
  exportFeedView,
  fetchFeedPage,
  formatRelativeTime,
  isOpenableUrl,
  itemKey,
  loadFeedStates,
  primaryScore,
  saveFeedStates,
  toggleMarker,
  type ExportFormat,
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

/** 搜索防抖(G1):输入停顿 300ms 提交;Enter 立即提交 */
const SEARCH_DEBOUNCE_MS = 300;

/** 图析行截断上限(字符;10-03-vision-pipeline:metadata.image_ocr 可达全文,
 *  feed 屏只出单行摘要,CSS truncate 再兜底一行;全文看卡片展开态)。 */
const IMAGE_OCR_SUMMARY_CHARS = 160;

/** 图析单行摘要:压平空白 + 超限截断加省略号;空串返回 null(不渲染行)。 */
function imageOcrSummary(text: string | null | undefined): string | null {
  if (!text) return null;
  const flat = text.replace(/\s+/g, " ").trim();
  if (!flat) return null;
  if (flat.length <= IMAGE_OCR_SUMMARY_CHARS) return flat;
  return `${flat.slice(0, IMAGE_OCR_SUMMARY_CHARS)}…`;
}

/** 空流 CTA「运行第一个插件」的状态机(idle → starting → collecting → done/error) */
type RunCtaState =
  | { phase: "idle" }
  | { phase: "starting" }
  | { phase: "collecting"; runId: number }
  | { phase: "done" }
  | { phase: "error"; message: string };

/** 「打开原文」:plugin-shell open(受控 shell:allow-open,scope 仅 https?://)。
 *  动态 import:浏览器直开(vitest/预览)不加载 Tauri 壳包,点击才触路。 */
async function openInBrowser(url: string): Promise<void> {
  const shell = await import("@tauri-apps/plugin-shell");
  await shell.open(url);
}

/** 绝对时间(展开态元信息行):YYYY-MM-DD HH:mm */
function formatAbsoluteTime(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return iso;
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${then.getFullYear()}-${pad(then.getMonth() + 1)}-${pad(then.getDate())} ${pad(then.getHours())}:${pad(then.getMinutes())}`;
}

function FeedCard({
  item,
  state,
  onMarkRead,
  onToggle,
  onOpenError,
}: {
  item: FeedItem;
  state: { read?: boolean; starred?: boolean; later?: boolean };
  onMarkRead: (item: FeedItem) => void;
  onToggle: (item: FeedItem, marker: "starred" | "later" | "read") => void;
  onOpenError: (message: string) => void;
}) {
  // 展开态属卡片本地(每次进屏重置;不与已读/星标本地态混存)
  const [expanded, setExpanded] = useState(false);
  const score = primaryScore(item);
  const time = formatRelativeTime(item.first_seen);
  const openable = isOpenableUrl(item.url);
  const expandable = Boolean(item.content) || Boolean(imageOcrSummary(item.image_ocr));
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
          {openable ? (
            <Button
              variant="ghost"
              size="icon"
              className="size-6"
              aria-label="打开原文"
              title={`在浏览器打开:${item.url}`}
              onClick={() =>
                void openInBrowser(item.url).catch((err) =>
                  onOpenError(err instanceof Error ? err.message : String(err)),
                )
              }
            >
              <ExternalLink className="size-3.5 text-muted-foreground" />
            </Button>
          ) : null}
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
        {expandable ? (
          <Button
            variant="ghost"
            size="icon"
            className="size-5"
            aria-label={expanded ? "收起条目" : "展开条目"}
            aria-expanded={expanded}
            onClick={() => setExpanded((current) => !current)}
          >
            {expanded ? (
              <ChevronDown className="size-3.5 text-muted-foreground" />
            ) : (
              <ChevronRight className="size-3.5 text-muted-foreground" />
            )}
          </Button>
        ) : null}
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
        expanded ? (
          // 展开态:全文 + 元信息(G2:C9 消号——正文与原文链接都在卡内)
          <div className="mt-1.5" data-testid={`feed-expanded-${item.id ?? itemKey(item)}`}>
            <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-foreground/90">
              {item.content}
            </p>
            <p className="mt-1.5 text-[11px] text-muted-foreground">
              首见 {formatAbsoluteTime(item.first_seen)}
              {item.pushed_at ? ` · 已推送 ${formatAbsoluteTime(item.pushed_at)}` : ""}
              {openable ? ` · ${item.url}` : ""}
            </p>
          </div>
        ) : (
          <p className="mt-1.5 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{item.content}</p>
        )
      ) : null}

      {imageOcrSummary(item.image_ocr) ? (
        <p
          className="mt-1.5 flex items-center gap-1.5 text-xs leading-relaxed text-muted-foreground"
          data-testid={`feed-image-ocr-${item.id ?? itemKey(item)}`}
        >
          <Badge variant="outline" className="shrink-0" title="图析:配图 OCR 文本摘要">
            图
          </Badge>
          <span className="min-w-0 truncate" title={item.image_ocr ?? undefined}>
            {imageOcrSummary(item.image_ocr)}
          </span>
        </p>
      ) : null}
    </div>
  );
}

/**
 * 情报流:条目卡片瀑布 + 未读/星标/稍后读三态(本地态,localStorage 持久)
 * + 游标分页加载(见 ./api 的协议缺口注记)+ 服务端搜索(G1,防抖/Enter
 * 提交,query 随游标透传)+ 顶栏品类服务端过滤(C8,Outlet context)+ 卡片
 * 展开/打开原文(G2)+ 导出当前视图(G3,dialog.save → feed.export)。
 */
export function FeedScreen() {
  const navigate = useNavigate();
  // 顶栏品类(C8):路由 Outlet context 下发;直渲染(无 Outlet 父级)容错 null
  const outlet = useOutletContext<CategoryFilterContext | null>();
  const category = outlet?.category ?? null;
  const [items, setItems] = useState<FeedItem[]>([]);
  const [states, setStates] = useState<FeedStateMap>({});
  const [filter, setFilter] = useState<FeedFilter>("unread");
  const [cursor, setCursor] = useState<string | null>(null);
  /** 复合游标第二键(同刻条目翻页不跳不重;C1) */
  const [cursorId, setCursorId] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<SidecarRequestError | null>(null);
  /** health.first_run:空流时区分「无插件(首跑初始化)」与「有插件未采集」 */
  const [firstRun, setFirstRun] = useState(false);
  const [cta, setCta] = useState<RunCtaState>({ phase: "idle" });
  /** G1 搜索:输入框即时值 / 已提交值(防抖 300ms 或 Enter) */
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  /** G3 导出:格式选择 + 进行中 + 回显;G2 打开原文失败回显 */
  const [exportFormat, setExportFormat] = useState<ExportFormat>("jsonl");
  const [exporting, setExporting] = useState(false);
  const [exportNote, setExportNote] = useState<string | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);

  useEffect(() => {
    setStates(loadFeedStates());
  }, []);

  // 防抖提交(G1):输入停顿 300ms → query(触发服务端重查);Enter 即时
  useEffect(() => {
    const next = searchInput.trim();
    if (next === query) return;
    const timer = setTimeout(() => setQuery(next), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchInput, query]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await fetchFeedPage({ cursor: null, cursorId: null, category, query });
      setItems(page.items);
      setCursor(page.nextCursor);
      setCursorId(page.nextCursorId);
      setHasMore(page.hasMore);
      if (page.items.length === 0) {
        // 空结果才追问 health(一次 RPC):空态文案按有无插件分叉
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
  }, [category, query]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const loadMore = useCallback(async () => {
    if (loadingMore || cursor === null) return;
    setLoadingMore(true);
    try {
      const page = await fetchFeedPage({ cursor, cursorId, category, query });
      setCursor(page.nextCursor);
      setCursorId(page.nextCursorId);
      setItems((current) => {
        const merged = appendFeedPage(current, page);
        // 追加 0 条防御判停(复合游标下不应发生;保留兜底防死循环)
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
  }, [cursor, cursorId, loadingMore, category, query]);

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

  /** G3 导出当前视图:dialog.save → feed.export;回显 path/count(取消 = 静默) */
  const exportCurrentView = useCallback(async () => {
    setExporting(true);
    setExportNote(null);
    try {
      const outcome = await exportFeedView({ format: exportFormat, category, query });
      if (outcome.path !== null) {
        setExportNote(`已导出 ${outcome.count} 条 → ${outcome.path}(${outcome.bytes} 字节)`);
      }
    } catch (err) {
      setExportNote(
        `导出失败:${err instanceof SidecarRequestError ? `${err.code}: ${err.message}` : String(err)}`,
      );
    } finally {
      setExporting(false);
    }
  }, [exportFormat, category, query]);

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

  const searchActive = query !== "";

  return (
    <div className="flex flex-col gap-4 pb-6">
      <PageHeader
        title="情报流"
        description="卡片瀑布:未读 / 星标 / 稍后读(本地态,随浏览器存储持久)"
        actions={
          <div className="flex items-center gap-1.5">
            <Button
              variant={exportFormat === "jsonl" ? "secondary" : "ghost"}
              size="sm"
              aria-pressed={exportFormat === "jsonl"}
              title={`JSON Lines 格式(默认文件名 ${defaultExportName("jsonl")})`}
              onClick={() => setExportFormat("jsonl")}
            >
              JSONL
            </Button>
            <Button
              variant={exportFormat === "csv" ? "secondary" : "ghost"}
              size="sm"
              aria-pressed={exportFormat === "csv"}
              title={`CSV 格式(默认文件名 ${defaultExportName("csv")})`}
              onClick={() => setExportFormat("csv")}
            >
              CSV
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void exportCurrentView()}
              disabled={exporting}
              title="导出当前过滤视图(品类 × 搜索词)为本地文件"
            >
              <Download className={exporting ? "size-3.5 animate-pulse" : "size-3.5"} />
              {exporting ? "导出中…" : "导出当前视图"}
            </Button>
            <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
              <RefreshCw className={loading ? "size-3.5 animate-spin" : "size-3.5"} />
              刷新
            </Button>
          </div>
        }
      />

      <div className="flex flex-wrap items-center gap-1 px-6">
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
        {searchActive ? (
          <span className="text-[11px] text-muted-foreground" data-testid="feed-search-scope">
            服务端搜索「{query}」{category ? ` × 品类 ${category}` : ""} × 本地
            {FILTERS.find((entry) => entry.key === filter)?.label}过滤
          </span>
        ) : null}
        <div className="ml-auto flex items-center gap-1.5">
          <Search className="size-3.5 text-muted-foreground" aria-hidden />
          <input
            type="search"
            value={searchInput}
            aria-label="搜索条目"
            placeholder="搜索标题 / 摘要 / 来源(服务端全库)"
            className="h-7 w-56 rounded-md border border-border bg-background px-2 text-xs text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            onChange={(event) => setSearchInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") setQuery(searchInput.trim());
            }}
          />
        </div>
      </div>

      <div className="flex flex-col gap-2 px-6">
        {exportNote ? (
          <p className="text-xs text-muted-foreground" data-testid="feed-export-result">
            {exportNote}
          </p>
        ) : null}
        {openError ? (
          <p className="text-xs text-destructive" data-testid="feed-open-error">
            打开原文失败:{openError}
          </p>
        ) : null}

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
          items.length === 0 && searchActive ? (
            <Card data-testid="feed-search-empty">
              <CardContent className="p-0">
                <EmptyState
                  title="没有匹配的条目"
                  description={`服务端全库搜索「${query}」零命中;换个关键词,或清空搜索看全部条目。`}
                />
              </CardContent>
            </Card>
          ) : items.length === 0 && firstRun ? (
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
              onOpenError={setOpenError}
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
