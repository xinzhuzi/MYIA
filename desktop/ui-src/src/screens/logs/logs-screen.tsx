import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw, Terminal } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SidecarRequestError } from "@/lib/api";
import type { RunEntry, SidecarEvent, UnlistenFn } from "@/lib/api";

import { buildRunRows, eventToRow, isRowError, isRowWarn, loadRunLogs, loadRuns, subscribeRunEvents, tailToRows } from "./api";
import type { LogRow } from "./api";

/** run 状态 → 列表徽标(退出码语义 0/1/2/3,见 types.ts RunExitStatus) */
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
  }
}

function RunListRow({
  run,
  selected,
  onSelect,
}: {
  run: ReturnType<typeof buildRunRows>[number];
  selected: boolean;
  onSelect: (runId: number) => void;
}) {
  const badge = runBadge(run.status, run.state);
  return (
    <button
      type="button"
      data-testid={`run-row-${run.runId}`}
      aria-pressed={selected}
      onClick={() => onSelect(run.runId)}
      className={`flex w-full items-center justify-between gap-2 rounded-md border px-2.5 py-2 text-left transition-colors ${
        selected ? "border-primary/40 bg-primary/10" : "border-transparent hover:bg-accent"
      }`}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="font-mono text-[11px] text-muted-foreground">#{run.runId}</span>
        <span className="truncate text-xs font-medium text-foreground">{run.category}</span>
        {run.dry ? <Badge variant="outline">dry</Badge> : null}
      </div>
      <div className="flex shrink-0 items-center gap-2 text-[11px] text-muted-foreground">
        {run.itemCount !== null ? <span>{run.itemCount} 条</span> : null}
        <span className="font-mono">{run.durationText}</span>
        <Badge variant={badge.variant}>{badge.label}</Badge>
      </div>
    </button>
  );
}

function LogRowView({ row }: { row: LogRow }) {
  if (row.stream === "system") {
    return (
      <p data-testid="log-system-row" className="text-muted-foreground/80">
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
          ? "rounded-sm bg-destructive/15 px-1 text-destructive" // 错误行高亮(红)
          : warn
            ? "px-1 text-warning/85" // stderr 非错误行(WARNING+ 日志)弱警示
            : "px-1 text-foreground/90"
      }
    >
      <span className="mr-1.5 text-muted-foreground/60">·</span>
      {row.text}
    </p>
  );
}

/**
 * 采集日志:左列 run 列表(状态/耗时/条目数),右侧选中 run 的日志终端。
 * 渲染 = logs.tail 历史打底 + sidecar://event 流式续播;错误行红底高亮。
 */
export function LogsScreen() {
  const [runs, setRuns] = useState<RunEntry[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [rows, setRows] = useState<LogRow[]>([]);
  /** tail 相对环形缓冲是否被截断(lines 上限内只回尾部;如实展示) */
  const [truncated, setTruncated] = useState(false);
  const [live, setLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<SidecarRequestError | null>(null);
  const eventSeq = useRef(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const refreshRuns = useCallback(async () => {
    try {
      const list = await loadRuns();
      setRuns(list);
      // 无选中时自动跟踪最新 run(列表新→旧,见 entry.py `_m_run_status`);
      // 用户已选中则尊重其选择
      setSelectedId((current) => current ?? list[0]?.run_id ?? null);
    } catch (err) {
      setError(
        err instanceof SidecarRequestError
          ? err
          : new SidecarRequestError({ code: "transport_error", path: "$", message: String(err) }),
      );
    }
  }, []);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      await refreshRuns();
      setLoading(false);
    })();
  }, [refreshRuns]);

  // 选中 run 变化:tail 打底 + 订阅事件续播(卸载/切换时退订并丢弃迟到结果)
  useEffect(() => {
    let cancelled = false;
    setRows([]);
    setTruncated(false);
    setError(null);
    // 显式标注:订阅链保持 Promise<UnlistenFn>(供清理退订);拒绝单列处理
    const unlisten: Promise<UnlistenFn> = subscribeRunEvents((event: SidecarEvent) => {
      // 采集日志只续播 run 域事件(log/progress/completed;10-03-vision-pipeline
      // 拆屏后协议已无 image.* 事件,过滤保留为穷尽防御)
      if (event.type !== "log" && event.type !== "progress" && event.type !== "completed") return;
      if (event.type === "completed") {
        // 任意 run 完成都刷新列表;选中 run 另行落一行完成摘要
        void refreshRuns();
      }
      if (event.run_id !== selectedId) return;
      eventSeq.current += 1;
      setRows((current) => [...current, eventToRow(event, eventSeq.current)]);
    }).then((fn) => {
      if (!cancelled) setLive(true);
      return fn;
    });
    unlisten.catch(() => {
      // 事件通道不可用(如浏览器直开):tail 历史仍可用,不拦界面
    });
    void (async () => {
      try {
        const tail = await loadRunLogs(selectedId);
        if (cancelled) return;
        setRows(tailToRows(tail));
        setTruncated(tail.truncated);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof SidecarRequestError
              ? err
              : new SidecarRequestError({ code: "transport_error", path: "$", message: String(err) }),
          );
        }
      }
    })();
    return () => {
      cancelled = true;
      setLive(false);
      void unlisten.then((fn) => fn()).catch(() => undefined);
    };
  }, [selectedId, refreshRuns]);

  // 新行到达自动滚底(终端惯例)
  useEffect(() => {
    const el = scrollRef.current;
    if (el !== null) el.scrollTop = el.scrollHeight;
  }, [rows]);

  const runRows = useMemo(() => buildRunRows(runs), [runs]);
  const selectedRun = useMemo(() => runs.find((run) => run.run_id === selectedId) ?? null, [runs, selectedId]);
  const errorCount = rows.filter(isRowError).length;

  return (
    <div className="flex flex-col gap-4 pb-6">
      <PageHeader
        title="采集日志"
        description="run 瀑布列表 + 选中 run 的流式终端(logs.tail 打底,事件续播,错误行高亮)"
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

      <div className="grid grid-cols-1 gap-4 px-6 lg:grid-cols-[minmax(280px,380px)_1fr]">
        {/* run 列表 */}
        <Card>
          <CardHeader>
            <CardTitle>run 列表</CardTitle>
            <CardDescription>状态 / 耗时 / 条目数(新→旧)</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-1">
            {loading && runs.length === 0 ? (
              <>
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </>
            ) : runRows.length === 0 ? (
              <EmptyState
                compact
                title="还没有 run 记录"
                description="从源管理触发一次采集后,run 将按新→旧出现在这里"
              />
            ) : (
              runRows.map((run) => (
                <RunListRow key={run.runId} run={run} selected={run.runId === selectedId} onSelect={setSelectedId} />
              ))
            )}
          </CardContent>
        </Card>

        {/* 日志终端 */}
        <div className="flex h-[28rem] flex-col overflow-hidden rounded-lg border border-border bg-[#070b14]">
          <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-1.5">
            <div className="flex items-center gap-2">
              <Terminal className="size-3.5 text-muted-foreground" />
              <span data-testid="terminal-title" className="font-mono text-[11px] text-muted-foreground">
                {selectedId === null
                  ? "logs.tail — 未选中 run"
                  : `logs.tail run_id=${selectedId}${selectedRun ? ` · ${selectedRun.record?.category ?? selectedRun.yaml}` : ""}`}
              </span>
            </div>
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              {truncated ? <Badge variant="outline">缓冲截断</Badge> : null}
              {errorCount > 0 ? <Badge variant="destructive">{errorCount} 错误行</Badge> : null}
              <span className="font-mono">{rows.length} 行</span>
              <Badge variant={live ? "ok" : "unknown"}>{live ? "实时跟踪中" : "未跟踪"}</Badge>
            </div>
          </div>
          <div ref={scrollRef} data-testid="terminal-body" className="min-h-0 flex-1 overflow-y-auto p-3 font-mono text-xs leading-relaxed">
            {rows.length === 0 ? (
              <p className="text-muted-foreground">
                <span className="text-brand-from">▍</span>
                {selectedId === null
                  ? " 选中左侧一个 run 查看其日志;实时输出将自动续播至此终端"
                  : " 该 run 暂无日志(环形缓冲只保留最近 4000 行)"}
              </p>
            ) : (
              rows.map((row) => <LogRowView key={row.key} row={row} />)
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
