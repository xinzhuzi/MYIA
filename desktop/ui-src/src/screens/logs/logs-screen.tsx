import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, RefreshCw } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SidecarRequestError } from "@/lib/api";
import type { RunEntry, SidecarEvent, UnlistenFn } from "@/lib/api";

import { buildRunRows, eventToRow, isRowError, isRowWarn, loadRunLogs, loadRuns, subscribeRunEvents, tailToRows } from "./api";
import type { LogRow, RunRowModel } from "./api";

/**
 * 采集日志(D4 结构性重做,对标 Crawlab 日志 UI + Kestra run 视图;Crawlab BSD-3
 * 可直借,licenses.md 实核):单列 run 瀑布(新→旧),每 run 一段折叠组——
 * 统计行(耗时/条数/状态/错误行数)+ 等宽字体日志体;错误行 dead 色高亮;
 * 展开组新行到达自动滚底(终端惯例,Crawlab revealLine 同款)。
 * 数据面不变:run.status + logs.tail(展开时惰性拉取,缓存)+ sidecar://event 续播。
 */

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

function LogRowView({ row }: { row: LogRow }) {
  if (row.stream === "system") {
    return (
      <p data-testid="log-system-row" className="whitespace-pre-wrap break-words text-muted-foreground/80">
        <span className="mr-1.5 text-brand-from">▸</span>
        {row.text}
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
          ? "whitespace-pre-wrap break-words rounded-sm bg-dead/15 px-1 text-dead" // 错误行 dead 色高亮(D4)
          : warn
            ? "whitespace-pre-wrap break-words px-1 text-warning/85" // WARNING 级 / 裸 stderr 诊断行弱警示
            : "whitespace-pre-wrap break-words px-1 text-foreground/90"
      }
    >
      <span className="mr-1.5 text-muted-foreground/60">·</span>
      {row.text}
    </p>
  );
}

/** 折叠组头 = 每 run 统计行:品类 + 耗时/条数/状态/错误行数(Kestra 步骤头同款) */
function RunGroupHeader({
  run,
  badge,
  errorCount,
  expanded,
  onToggle,
}: {
  run: RunRowModel;
  badge: { variant: "default" | "ok" | "warning" | "destructive" | "unknown"; label: string };
  errorCount: number;
  expanded: boolean;
  onToggle: (runId: number) => void;
}) {
  return (
    <button
      type="button"
      data-testid={`run-group-header-${run.runId}`}
      aria-expanded={expanded}
      aria-controls={`run-group-body-${run.runId}`}
      onClick={() => onToggle(run.runId)}
      className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors duration-(--duration-fast) hover:bg-accent/60"
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
          <span data-testid={`run-error-count-${run.runId}`} className="font-medium text-dead">
            {errorCount} 错误行
          </span>
        ) : null}
        {run.itemCount !== null ? <span>{run.itemCount} 条</span> : null}
        <span className="font-mono">{run.durationText}</span>
        <Badge variant={badge.variant}>{badge.label}</Badge>
      </span>
    </button>
  );
}

/**
 * 展开组的日志体:等宽终端块 + 瘦元信息条(logs.tail 来源/行数/截断/实时),
 * 新行到达自动滚底(Crawlab TaskDetailTabLogs revealLine 同款,无条件滚)。
 */
function RunLogBody({
  runId,
  rows,
  truncated,
  loadError,
  running,
  live,
}: {
  runId: number;
  rows: LogRow[] | undefined;
  truncated: boolean;
  loadError: string | null;
  running: boolean;
  live: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const errorCount = rows?.filter(isRowError).length ?? 0;

  // 自动滚底(D4):行集变化即贴底(终端惯例;展开瞬间 rows 未必到位,依赖数组带 rows 兜两拍)
  useEffect(() => {
    const el = scrollRef.current;
    if (el !== null) el.scrollTop = el.scrollHeight;
  }, [rows, runId]);

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
          {rows !== undefined ? <span className="font-mono">{rows.length} 行</span> : null}
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
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground">
            <span className="text-brand-from">▍</span> 该 run 暂无日志(环形缓冲只保留最近 4000 行)
          </p>
        ) : (
          rows.map((row) => <LogRowView key={row.key} row={row} />)
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
          ) : (
            runRows.map((run) => {
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
                  />
                  {expanded.has(run.runId) ? (
                    <RunLogBody
                      runId={run.runId}
                      rows={rows}
                      truncated={truncatedByRun[run.runId] ?? false}
                      loadError={loadErrors[run.runId] ?? null}
                      running={run.state === "running"}
                      live={live}
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
