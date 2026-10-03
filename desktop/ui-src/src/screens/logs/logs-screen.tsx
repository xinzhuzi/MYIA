import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Loader2, RefreshCw, RotateCcw, Search, X } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { SidecarRequestError } from "@/lib/api";
import type { RunEntry, SidecarEvent, UnlistenFn } from "@/lib/api";

import {
  buildRunRows,
  eventToRow,
  filterRunRows,
  isRowError,
  isRowWarn,
  loadRunLogs,
  loadRuns,
  rerunRun,
  rowMatchesQuery,
  splitTextOnQuery,
  subscribeRunEvents,
  tailToRows,
} from "./api";
import type { LogRow, RunFilterStatus, RunRowModel } from "./api";

/**
 * 采集日志(D4 结构性重做,对标 Crawlab 日志 UI + Kestra run 视图;Crawlab BSD-3
 * 可直借,licenses.md 实核):单列 run 瀑布(新→旧),每 run 一段折叠组——
 * 统计行(耗时/条数/状态/错误行数)+ 等宽字体日志体;错误行 dead 色高亮;
 * 展开组新行到达自动滚底(终端惯例,Crawlab revealLine 同款)。
 * 数据面不变:run.status + logs.tail(展开时惰性拉取,缓存)+ sidecar://event 续播。
 *
 * G7 三件:①run 行重跑(骑 G4 手动触发通道 run.start,不新增协议;成功反馈
 * 新 run_id,新 run 经列表刷新自动展开跟随);②品类·状态过滤条(与源管理
 * compact 密度同款 select);③日志搜索(输入即过滤 + 命中片段高亮)。
 */

/** 状态过滤键 → 过滤选项文案(与 runBadge 同词表) */
const STATUS_FILTER_OPTIONS: Array<{ value: RunFilterStatus; label: string }> = [
  { value: "running", label: "运行中" },
  { value: "success", label: "成功" },
  { value: "partial", label: "部分" },
  { value: "config_error", label: "配置" },
  { value: "failed", label: "失败" },
  { value: "cancelled", label: "已取消" },
];

/** run 状态 → 列表徽标(退出码语义 0/1/2/3 + cancelled,见 types.ts RunExitStatus) */
function runBadge(status: RunEntry["status"], state: RunEntry["state"]) {
  if (state === "running" || status === null) {
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
    case "cancelled":
      // run.cancel 信号终局(C2,v1.1.2 桌面对齐批)
      return { variant: "unknown" as const, label: "已取消" };
  }
}

/** 命中片段 <mark>:警示底 + 前景保持行色系(只标记不改语,WCAG 不降级) */
function HighlightedText({ text, needle }: { text: string; needle: string }) {
  if (needle === "") return <>{text}</>;
  return (
    <>
      {splitTextOnQuery(text, needle).map((part, index) =>
        part.hit ? (
          <mark
            key={index}
            data-testid="log-search-hit"
            className="rounded-[2px] bg-warning/30 text-foreground underline decoration-warning/60 decoration-1 underline-offset-2"
          >
            {part.text}
          </mark>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </>
  );
}

function LogRowView({ row, needle }: { row: LogRow; needle: string }) {
  if (row.stream === "system") {
    return (
      <p data-testid="log-system-row" className="whitespace-pre-wrap break-words text-muted-foreground/80">
        <span className="mr-1.5 text-brand-from">▸</span>
        <HighlightedText text={row.text} needle={needle} />
      </p>
    );
  }
  const error = isRowError(row);
  const warn = isRowWarn(row);
  return (
    <p
      data-testid="log-row"
      data-error={error ? "true" : undefined}
      data-warn={warn ? "true" : undefined}
      className={
        error
          ? // 错误行 dead 色高亮(D4;P2 对比实算:bg-dead/10 叠 bg-sidebar ≈ 4.67:1,
            // 原 bg-dead/15 ≈ 4.43:1 不达 WCAG AA 正文 4.5,降档到 /10)
            "whitespace-pre-wrap break-words rounded-sm bg-dead/10 px-1 text-dead"
          : warn
            ? "whitespace-pre-wrap break-words px-1 text-warning/85" // WARNING 级 / 裸 stderr 诊断行弱警示
            : "whitespace-pre-wrap break-words px-1 text-foreground/90"
      }
    >
      <span className="mr-1.5 text-muted-foreground/60">·</span>
      <HighlightedText text={row.text} needle={needle} />
    </p>
  );
}

/** 重跑状态反馈(G7):starting 打转 → triggered 报新 run_id → 8s 后自动隐去 */
type RerunState =
  | { phase: "starting" }
  | { phase: "triggered"; runId: number }
  | { phase: "error"; message: string };

/**
 * 折叠组头 = 每 run 统计行:品类 + 耗时/条数/状态/错误行数(Kestra 步骤头同款)
 * + 行尾重跑操作(G7)。折叠控件与重跑按钮是兄弟节点(button 不可嵌套);
 * testid/aria-expanded 留在折叠按钮上(它才是展开语义的owner)。
 */
function RunGroupHeader({
  run,
  badge,
  errorCount,
  expanded,
  onToggle,
  rerun,
  onRerun,
}: {
  run: RunRowModel;
  badge: { variant: "default" | "ok" | "warning" | "destructive" | "unknown"; label: string };
  errorCount: number;
  expanded: boolean;
  onToggle: (runId: number) => void;
  rerun: RerunState | undefined;
  onRerun: (run: RunRowModel) => void;
}) {
  const starting = rerun?.phase === "starting";
  return (
    <div className="flex items-center gap-1 pr-2 transition-colors duration-(--duration-fast) hover:bg-accent/60">
      <button
        type="button"
        data-testid={`run-group-header-${run.runId}`}
        aria-expanded={expanded}
        aria-controls={`run-group-body-${run.runId}`}
        onClick={() => onToggle(run.runId)}
        className="flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2.5 text-left"
      >
        <ChevronDown
          aria-hidden
          className={`size-3.5 shrink-0 text-muted-foreground transition-transform duration-(--duration-fast) ${expanded ? "" : "-rotate-90"}`}
        />
        <span className="shrink-0 font-mono text-xs text-muted-foreground">#{run.runId}</span>
        <span className="min-w-0 truncate text-sm font-medium text-foreground">{run.category}</span>
        {run.dry ? <Badge variant="outline">dry</Badge> : null}
        <span className="ml-auto flex shrink-0 items-center gap-2 text-2xs text-muted-foreground">
          {errorCount > 0 ? (
            <span data-testid={`run-error-count-${run.runId}`} className="font-medium text-[#ff6b70]">
              {/* #ff6b70 而非 text-dead:组头 hover 底 accent/60 上 4.35<4.5,#ff6b70 实算 5.5+(WCAG) */}
              {errorCount} 错误行
            </span>
          ) : null}
          {run.itemCount !== null ? <span>{run.itemCount} 条</span> : null}
          <span className="font-mono">{run.durationText}</span>
          <Badge variant={badge.variant}>{badge.label}</Badge>
        </span>
      </button>
      {rerun?.phase === "triggered" ? (
        <span
          data-testid={`run-rerun-ok-${run.runId}`}
          className="shrink-0 text-2xs font-medium text-ok"
          title={`新 run #${rerun.runId} 已启动,正在展开跟随`}
        >
          已触发 #{rerun.runId}
        </span>
      ) : null}
      {rerun?.phase === "error" ? (
        <span
          data-testid={`run-rerun-err-${run.runId}`}
          className="max-w-44 shrink-0 truncate text-2xs text-destructive"
          title={rerun.message}
        >
          {rerun.message}
        </span>
      ) : null}
      <Button
        variant="ghost"
        size="icon"
        className="size-6 shrink-0 text-muted-foreground"
        data-testid={`run-rerun-${run.runId}`}
        aria-label={`重跑 run #${run.runId}`}
        title={`重跑该 run(run.start 回放 ${run.yaml}${run.dry ? ",dry" : ""})`}
        disabled={starting}
        onClick={() => onRerun(run)}
      >
        {starting ? (
          <Loader2 aria-hidden className="size-3.5 animate-spin" />
        ) : (
          <RotateCcw aria-hidden className="size-3.5" />
        )}
      </Button>
    </div>
  );
}

/**
 * 展开组的日志体:等宽终端块 + 瘦元信息条(logs.tail 来源/行数/截断/实时),
 * 新行到达自动滚底(Crawlab TaskDetailTabLogs revealLine 同款,无条件滚)。
 * 搜索态(G7):needle 非空时行集即时过滤(命中才显示)+ 命中片段高亮,
 * 元信息条报「命中 X / Y 行」。
 */
function RunLogBody({
  runId,
  rows,
  truncated,
  loadError,
  running,
  live,
  needle,
}: {
  runId: number;
  rows: LogRow[] | undefined;
  truncated: boolean;
  loadError: string | null;
  running: boolean;
  live: boolean;
  needle: string;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const searching = needle !== "";
  const shownRows = useMemo(
    () => (rows === undefined ? undefined : searching ? rows.filter((row) => rowMatchesQuery(row, needle)) : rows),
    [rows, searching, needle],
  );
  const errorCount = rows?.filter(isRowError).length ?? 0;

  // 自动滚底(D4):行集变化即贴底(终端惯例;展开瞬间 rows 未必到位,依赖数组带 rows 兜两拍)
  useEffect(() => {
    const el = scrollRef.current;
    if (el !== null) el.scrollTop = el.scrollHeight;
  }, [shownRows, runId]);

  return (
    <div id={`run-group-body-${runId}`} data-testid={`run-log-${runId}`} className="border-t border-border bg-sidebar">
      <div
        data-testid={`run-log-meta-${runId}`}
        className="flex items-center justify-between gap-2 px-3 py-1.5 text-2xs text-muted-foreground"
      >
        <span className="truncate font-mono">logs.tail run_id={runId}</span>
        <span className="flex shrink-0 items-center gap-2">
          {truncated ? <Badge variant="outline">缓冲截断</Badge> : null}
          {errorCount > 0 ? <Badge variant="destructive">{errorCount} 错误行</Badge> : null}
          {shownRows !== undefined ? (
            searching ? (
              <span data-testid={`run-log-hits-${runId}`} className="font-mono">
                命中 {shownRows.length} / {rows?.length ?? 0} 行
              </span>
            ) : (
              <span className="font-mono">{rows?.length ?? 0} 行</span>
            )
          ) : null}
          {running ? <Badge variant={live ? "ok" : "unknown"}>{live ? "实时跟踪中" : "未跟踪"}</Badge> : null}
        </span>
      </div>
      <div
        ref={scrollRef}
        className="max-h-[26rem] overflow-y-auto px-3 pb-3 font-mono text-xs leading-relaxed"
      >
        {loadError !== null ? (
          <p className="text-dead">
            <span className="mr-1.5">●</span>日志加载失败({loadError})
          </p>
        ) : rows === undefined ? (
          <p className="text-muted-foreground">
            <span className="text-brand-from">▍</span> 正在拉取 logs.tail…
          </p>
        ) : searching && shownRows !== undefined && shownRows.length === 0 ? (
          <p className="text-muted-foreground">
            <span className="text-brand-from">▍</span> 无命中行(搜索词过滤掉了该 run 的全部日志)
          </p>
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground">
            <span className="text-brand-from">▍</span> 该 run 暂无日志(环形缓冲只保留最近 4000 行)
          </p>
        ) : (
          shownRows?.map((row) => <LogRowView key={row.key} row={row} needle={needle} />)
        )}
      </div>
    </div>
  );
}

export function LogsScreen() {
  const [runs, setRuns] = useState<RunEntry[]>([]);
  /** 逐 run 日志行:展开时 tail 打底 + 事件续播(未加载的 run 先缓冲,加载时合并) */
  const [rowsByRun, setRowsByRun] = useState<Record<number, LogRow[]>>({});
  const [truncatedByRun, setTruncatedByRun] = useState<Record<number, boolean>>({});
  /** 逐 run tail 拉取失败(code:message);成功即清除,重展开可重试 */
  const [loadErrors, setLoadErrors] = useState<Record<number, string>>({});
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(new Set());
  const [live, setLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<SidecarRequestError | null>(null);
  const eventSeq = useRef(0);
  /** 已成功 tail 打底的 run(重展开不重拉,缓存驻屏) */
  const loadedRunsRef = useRef<Set<number>>(new Set());
  /** 拉取在途的 run(防双发) */
  const fetchingRef = useRef<Set<number>>(new Set());
  /** 已见过的 run_id(自动跟随=新出现的 running run 才展开,不打扰用户折叠态) */
  const knownRunIdsRef = useRef<Set<number>>(new Set());

  /** 展开 run 的日志惰性加载(Kestra 步骤展开同款):首次展开才 logs.tail;
   *  加载期间到达的事件行已在缓冲,合并时排在 tail 历史之后(事件比快照新) */
  const ensureTail = useCallback((runId: number) => {
    if (loadedRunsRef.current.has(runId) || fetchingRef.current.has(runId)) return;
    fetchingRef.current.add(runId);
    void loadRunLogs(runId)
      .then((tail) => {
        loadedRunsRef.current.add(runId);
        setRowsByRun((prev) => ({ ...prev, [runId]: [...tailToRows(tail), ...(prev[runId] ?? [])] }));
        setTruncatedByRun((prev) => ({ ...prev, [runId]: tail.truncated }));
        setLoadErrors((prev) => {
          if (!(runId in prev)) return prev;
          const next = { ...prev };
          delete next[runId];
          return next;
        });
      })
      .catch((err) => {
        const message = err instanceof SidecarRequestError ? `${err.code} · ${err.message}` : String(err);
        setLoadErrors((prev) => ({ ...prev, [runId]: message }));
      })
      .finally(() => {
        fetchingRef.current.delete(runId);
      });
  }, []);

  const refreshRuns = useCallback(async () => {
    try {
      const list = await loadRuns();
      setRuns(list);
      // 跟随策略(Crawlab 活动任务跟踪同款):初次进屏展开最新 run;
      // 之后仅新出现的 running run 自动展开跟随,用户折叠过的不强扒
      const known = knownRunIdsRef.current;
      let followId: number | null = null;
      if (known.size === 0 && list.length > 0) {
        followId = list[0].run_id;
      } else {
        const newcomer = list.find((run) => run.state === "running" && !known.has(run.run_id));
        followId = newcomer ? newcomer.run_id : null;
      }
      if (followId !== null) {
        const target = followId;
        setExpanded((prev) => (prev.has(target) ? prev : new Set(prev).add(target)));
        ensureTail(target);
      }
      knownRunIdsRef.current = new Set(list.map((run) => run.run_id));
    } catch (err) {
      setError(
        err instanceof SidecarRequestError
          ? err
          : new SidecarRequestError({ code: "transport_error", path: "$", message: String(err) }),
      );
    }
  }, [ensureTail]);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      await refreshRuns();
      setLoading(false);
    })();
  }, [refreshRuns]);

  /** 事件流订阅(挂屏一次,不随展开态变化):任意 run 的事件按 run_id 归组缓冲;
   *  completed 另触发列表刷新(终态/耗时落表)。 */
  useEffect(() => {
    let cancelled = false;
    const unlisten: Promise<UnlistenFn> = subscribeRunEvents((event: SidecarEvent) => {
      // 采集日志只续播 run 域事件(log/progress/completed;10-03-vision-pipeline
      // 拆屏后协议已无 image.* 事件,过滤保留为穷尽防御)
      if (event.type !== "log" && event.type !== "progress" && event.type !== "completed") return;
      if (event.type === "completed") {
        void refreshRuns();
      }
      eventSeq.current += 1;
      const row = eventToRow(event, eventSeq.current);
      // run 域事件必带 run_id;null 分支仅为类型穷尽(提取局部量以保持闭包内收窄)
      const runId = row.runId;
      if (runId === null) return;
      setRowsByRun((prev) => ({ ...prev, [runId]: [...(prev[runId] ?? []), row] }));
    }).then((fn) => {
      if (!cancelled) setLive(true);
      return fn;
    });
    unlisten.catch(() => {
      // 事件通道不可用(如浏览器直开):tail 历史仍可用,不拦界面
    });
    return () => {
      cancelled = true;
      setLive(false);
      void unlisten.then((fn) => fn()).catch(() => undefined);
    };
  }, [refreshRuns]);

  const handleToggle = useCallback(
    (runId: number) => {
      if (!expanded.has(runId)) ensureTail(runId);
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(runId)) next.delete(runId);
        else next.add(runId);
        return next;
      });
    },
    [expanded, ensureTail],
  );

  const runRows = useMemo(() => buildRunRows(runs), [runs]);

  // ---- G7:过滤(品类·状态)+ 搜索 + 重跑 ---------------------------------

  const [categoryFilter, setCategoryFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<RunFilterStatus | "all">("all");
  const [query, setQuery] = useState("");
  /** 逐 run 重跑反馈(starting/triggered/error;8s 自动隐去) */
  const [rerunByRun, setRerunByRun] = useState<Record<number, RerunState>>({});
  const rerunTimersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  const needle = query.trim().toLowerCase();
  const categories = useMemo(() => Array.from(new Set(runRows.map((row) => row.category))).sort(), [runRows]);
  const visibleRunRows = useMemo(
    () => filterRunRows(runRows, categoryFilter, statusFilter),
    [runRows, categoryFilter, statusFilter],
  );
  const filtering = categoryFilter !== "all" || statusFilter !== "all";

  const clearRerunFeedback = useCallback((runId: number) => {
    setRerunByRun((prev) => {
      if (!(runId in prev)) return prev;
      const next = { ...prev };
      delete next[runId];
      return next;
    });
  }, []);

  /** 反馈 8s 自动隐去(定时器挂 ref,卸载统一清) */
  const scheduleRerunClear = useCallback((runId: number) => {
    const timers = rerunTimersRef.current;
    const previous = timers.get(runId);
    if (previous !== undefined) clearTimeout(previous);
    timers.set(
      runId,
      setTimeout(() => {
        timers.delete(runId);
        clearRerunFeedback(runId);
      }, 8000),
    );
  }, [clearRerunFeedback]);

  useEffect(() => {
    const timers = rerunTimersRef.current;
    return () => {
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
    };
  }, []);

  /**
   * 重跑(G7①):骑 G4 手动触发通道 run.start 回放 yaml/dry/db——不新增协议;
   * 成功反馈新 run_id 并刷新列表(follow 策略自动展开新 running run 承担
   * 「正在跑」主反馈);失败如实报错误码。
   */
  const handleRerun = useCallback(
    (run: RunRowModel) => {
      setRerunByRun((prev) => ({ ...prev, [run.runId]: { phase: "starting" } }));
      void rerunRun(run)
        .then((started) => {
          setRerunByRun((prev) => ({ ...prev, [run.runId]: { phase: "triggered", runId: started.run_id } }));
          scheduleRerunClear(run.runId);
          void refreshRuns();
        })
        .catch((err) => {
          const message = err instanceof SidecarRequestError ? `${err.code} · ${err.message}` : String(err);
          setRerunByRun((prev) => ({ ...prev, [run.runId]: { phase: "error", message } }));
          scheduleRerunClear(run.runId);
        });
    },
    [refreshRuns, scheduleRerunClear],
  );

  const resetFilters = useCallback(() => {
    setCategoryFilter("all");
    setStatusFilter("all");
    setQuery("");
  }, []);

  return (
    <div className="flex flex-col gap-4 pb-6">
      <PageHeader
        title="采集日志"
        description="run 瀑布(新→旧)· 逐 run 耗时/条数统计 · 错误行高亮 · 展开组流式续播自动滚底"
        actions={
          <Button variant="outline" size="sm" onClick={() => void refreshRuns()} disabled={loading}>
            <RefreshCw className={loading ? "size-3.5 animate-spin" : "size-3.5"} />
            刷新
          </Button>
        }
      />

      {/* 过滤条(G7②③):品类/状态 select + 日志搜索;密度对齐源管理工具行(compact) */}
      <div className="flex flex-wrap items-center gap-2 px-6" data-testid="logs-filter-bar">
        <Select value={categoryFilter} onValueChange={setCategoryFilter}>
          <SelectTrigger
            size="sm"
            className="max-w-48 text-xs text-muted-foreground"
            aria-label="品类过滤"
            data-testid="filter-category"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部品类</SelectItem>
            {categories.map((category) => (
              <SelectItem key={category} value={category}>
                {category}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={statusFilter} onValueChange={(value) => setStatusFilter(value as RunFilterStatus | "all")}>
          <SelectTrigger
            size="sm"
            className="text-xs text-muted-foreground"
            aria-label="状态过滤"
            data-testid="filter-status"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            {STATUS_FILTER_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {filtering ? (
          <Button variant="ghost" size="sm" className="h-7 gap-1 px-2 text-xs text-muted-foreground" onClick={resetFilters}>
            <X aria-hidden className="size-3.5" />
            清除过滤
          </Button>
        ) : null}
        <span className="text-2xs text-muted-foreground" data-testid="logs-filter-count">
          {visibleRunRows.length} / {runRows.length} run
        </span>
        <div className="relative ml-auto">
          <Search aria-hidden className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground/70" />
          <Input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索日志(输入即过滤,命中高亮)"
            aria-label="搜索日志"
            data-testid="log-search-input"
            className="h-7 w-60 pl-7 font-mono text-xs"
          />
        </div>
      </div>

      {error ? (
        <div className="px-6">
          <Card data-testid="logs-error">
            <CardContent className="pt-1">
              <p className="text-sm font-medium text-destructive">
                采集日志不可用(sidecar 错误码 {error.code})
              </p>
              <p className="mt-1 text-xs text-muted-foreground">{error.message}</p>
            </CardContent>
          </Card>
        </div>
      ) : null}

      <div className="px-6">
        {/* run 瀑布:每 run 一段折叠组(Crawlab 运行瀑布 + Kestra 步骤折叠) */}
        <div className="flex flex-col gap-2" data-testid="run-waterfall">
          {loading && runs.length === 0 ? (
            <>
              <Skeleton className="h-12 w-full rounded-lg" />
              <Skeleton className="h-12 w-full rounded-lg" />
              <Skeleton className="h-12 w-full rounded-lg" />
            </>
          ) : runRows.length === 0 ? (
            <Card>
              <CardContent>
                <EmptyState
                  compact
                  title="还没有 run 记录"
                  description="从源管理触发一次采集后,run 将按新→旧出现在这里"
                />
              </CardContent>
            </Card>
          ) : visibleRunRows.length === 0 ? (
            <Card>
              <CardContent>
                <EmptyState
                  compact
                  title="没有匹配的 run"
                  description="品类/状态过滤或搜索词没有命中任何 run,放宽条件再试"
                />
                <div className="mt-3 flex justify-center">
                  <Button variant="outline" size="sm" onClick={resetFilters}>
                    清除过滤
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : (
            visibleRunRows.map((run) => {
              const badge = runBadge(run.status, run.state);
              const rows = rowsByRun[run.runId];
              const errorCount = rows?.filter(isRowError).length ?? 0;
              return (
                <div
                  key={run.runId}
                  data-testid={`run-group-${run.runId}`}
                  className="overflow-hidden rounded-lg border border-border bg-card animate-fade-in"
                >
                  <RunGroupHeader
                    run={run}
                    badge={badge}
                    errorCount={errorCount}
                    expanded={expanded.has(run.runId)}
                    onToggle={handleToggle}
                    rerun={rerunByRun[run.runId]}
                    onRerun={handleRerun}
                  />
                  {expanded.has(run.runId) ? (
                    <RunLogBody
                      runId={run.runId}
                      rows={rows}
                      truncated={truncatedByRun[run.runId] ?? false}
                      loadError={loadErrors[run.runId] ?? null}
                      running={run.state === "running"}
                      live={live}
                      needle={needle}
                    />
                  ) : null}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
