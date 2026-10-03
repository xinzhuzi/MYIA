import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, onSidecarEvent, SidecarRequestError } from "@/lib/api";
import type { TestCompletedEvent } from "@/lib/api";
import { YamlEditorDialog } from "@/screens/yaml-editor/yaml-editor-dialog";

import { ErrorBox } from "./error-box";
import {
  asSidecarError,
  formatScheduleRun,
  loadScheduleRows,
  loadSourcesData,
  summarizeTestCompleted,
  verifySourceRoundTrip,
  writeSourceToggle,
} from "./api";
import type { RoundTripCheck, ScheduleRow, SourcesData, SourceRow, TestOutcomeView } from "./api";
import { SourcesTable, sourceKey } from "./sources-table";

interface LoadState {
  status: "loading" | "error" | "ready";
  data: SourcesData | null;
  error: SidecarRequestError | null;
}

/** 一次启停写回的往返复核结果(成功/失败都结构化展示,不静默) */
interface ToggleOutcome {
  sourceName: string;
  next: boolean;
  check: RoundTripCheck | null;
  error: SidecarRequestError | null;
}

/** 试抓(C13)屏内态:发起后等 test.completed 事件;结果只驻屏内,v1 不跨屏留存 */
interface TestState {
  jobId: number;
  sourceName: string;
  /** 行键(pluginFile::sourceName,spinner 定位) */
  key: string;
}

/**
 * 源管理:插件/源表格(TanStack 排序/筛选/分页)+ 健康度三色 + 启停写回 + 试抓。
 *
 * 启停链路:ToggleSwitch → sources.write(契约见 api.ts)→ doctor({yamls:[file]})
 * 复核往返一致 → 刷新 health。任一步失败都回到结构化错误态(开关状态不动),
 * 绝不假装成功。
 *
 * 行「试抓」(C13,10-03-v112-desktop-parity):api.sourcesTest 异步 job(单飞,
 * 全表试抓禁点)→ test.completed 事件回屏,行上方回显引擎/条数/指纹判定或
 * 结构化错误;结果只驻屏内,v1 不做跨屏留存(如实注记)。
 *
 * 行「编辑」:当场弹出 YAML 编辑对话框(不离开本屏;dirty 关闭守卫在弹窗
 * 内)。编辑内核与配置编辑屏共享(use-yaml-file-editor),深链 /yaml-editor
 * ?file=… 仍可用(侧栏入口,本屏不再跳转)。
 *
 * 底部「排程一览」(G4,10-03-feed-ux):逐品类 schedule.preview 并发取
 * 未来 5 次运行时刻(Apify 式 Next runs 预览,防 cron 写错);单品类失败
 * 只塌该行(allSettled),预览失败不塌整区。
 */
export function SourcesScreen() {
  const [state, setState] = useState<LoadState>({ status: "loading", data: null, error: null });
  /** 本次会话写回获得的停用名单(品类文件 → 源名列表;来源=写回应答,权威) */
  const [disabledByFile, setDisabledByFile] = useState<Record<string, string[]>>({});
  const [pendingKeys, setPendingKeys] = useState<Set<string>>(new Set());
  const [outcome, setOutcome] = useState<ToggleOutcome | null>(null);
  /** 当场编辑的品类文件(null = 弹窗关闭) */
  const [editingFile, setEditingFile] = useState<string | null>(null);
  /** 试抓(C13):进行中 job(单飞)+ 最近一次结果回显 */
  const [testing, setTesting] = useState<TestState | null>(null);
  const [testOutcome, setTestOutcome] = useState<TestOutcomeView | null>(null);
  const testingRef = useRef<TestState | null>(null);
  testingRef.current = testing;
  /** 排程一览(G4):逐品类 schedule.preview;单品类失败不塌整区 */
  const [scheduleRows, setScheduleRows] = useState<ScheduleRow[] | null>(null);

  const reload = useCallback(async () => {
    setState({ status: "loading", data: null, error: null });
    try {
      const data = await loadSourcesData();
      setState({ status: "ready", data, error: null });
      // 排程一览随 reload 并发刷新;失败静默保留旧值(区块自带错误行,不塌屏)
      void loadScheduleRows(data.plugins)
        .then(setScheduleRows)
        .catch(() => undefined);
    } catch (error) {
      setState({ status: "error", data: null, error: asSidecarError(error) });
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** 编辑弹窗保存成功:刷新表格(编辑可能改了源名单/健康度,与启停写回后同款 reload) */
  const handleDialogSaved = useCallback(() => {
    void reload();
  }, [reload]);

  /** 行「试抓」(C13):sources.test 异步 job,单飞;结果等 test.completed 事件 */
  const handleTest = useCallback(async (row: SourceRow) => {
    setTestOutcome(null);
    try {
      const started = await api.sourcesTest({ file: row.pluginFile, source: row.sourceName });
      setTesting({
        jobId: started.job_id,
        sourceName: row.sourceName,
        key: sourceKey(row.pluginFile, row.sourceName),
      });
    } catch (error) {
      const failure = asSidecarError(error);
      setTestOutcome({
        sourceName: row.sourceName,
        ok: false,
        summary: `发起失败(${failure.code}):${failure.message}`,
      });
    }
  }, []);

  // test.completed 事件 → 行内回显(job_id 对账防串台;结果只驻屏内,v1 不跨屏)
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let disposed = false;
    void onSidecarEvent((event) => {
      if (event.type !== "test.completed") return;
      const testEvent = event as TestCompletedEvent;
      const current = testingRef.current;
      if (current === null || testEvent.job_id !== current.jobId) return;
      setTesting(null);
      setTestOutcome(summarizeTestCompleted(testEvent, current.sourceName));
    })
      .then((fn) => {
        if (disposed) fn();
        else unlisten = fn;
      })
      .catch(() => {
        // 浏览器直开无事件通道:发起错误已如实呈现,零行为变化
      });
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);

  const handleToggle = useCallback(
    async (row: SourceRow, next: boolean) => {
      const key = sourceKey(row.pluginFile, row.sourceName);
      setOutcome(null);
      setPendingKeys((prev) => new Set(prev).add(key));
      try {
        const result = await writeSourceToggle({
          file: row.pluginFile,
          ...(next ? { enable: [row.sourceName] } : { disable: [row.sourceName] }),
        });
        setDisabledByFile((prev) => ({ ...prev, [result.file]: result.disabled }));
        const check = await verifySourceRoundTrip(result.file, result.enabled);
        setOutcome({ sourceName: row.sourceName, next, check, error: null });
        await reload();
      } catch (error) {
        setOutcome({ sourceName: row.sourceName, next, check: null, error: asSidecarError(error) });
      } finally {
        setPendingKeys((prev) => {
          const nextSet = new Set(prev);
          nextSet.delete(key);
          return nextSet;
        });
      }
    },
    [reload],
  );

  const rows = state.data?.rows ?? [];
  const disabledEntries = Object.entries(disabledByFile).flatMap(([file, names]) =>
    names.map((name) => ({ file, name, key: sourceKey(file, name) })),
  );
  const disabledKeys = new Set(disabledEntries.map((entry) => entry.key));
  const summary = state.data?.summary;

  return (
    <div className="flex flex-col gap-4 pb-6">
      <PageHeader
        title="源管理"
        description="品类源的启停写回品类 YAML;改动被 myia run 识别(doctor 复核往返一致)"
        actions={
          <>
            {summary ? (
              <div className="flex items-center gap-1">
                <Badge variant="ok">正常 {summary.ok}</Badge>
                <Badge variant="warning">退化 {summary.degraded}</Badge>
                <Badge variant="destructive">失效 {summary.dead}</Badge>
                {summary.unknown > 0 ? <Badge variant="unknown">未知 {summary.unknown}</Badge> : null}
              </div>
            ) : null}
            <Button size="sm" variant="outline" onClick={() => void reload()} disabled={state.status === "loading"}>
              <RefreshCw className={state.status === "loading" ? "size-3.5 animate-spin" : "size-3.5"} />
              刷新
            </Button>
          </>
        }
      />

      {state.status === "error" && state.error ? (
        // 重试点击后 status 翻回 loading,本框随卸载(骨架屏接管),无需自旋态
        <ErrorBox error={state.error} onRetry={() => void reload()} />
      ) : null}

      {state.data?.storeError ? (
        <div
          role="alert"
          className="mx-6 rounded-md border border-warning/30 bg-warning/10 px-4 py-3 text-xs text-warning"
        >
          存储无法打开({state.data.storeError.error_type}):{state.data.storeError.message}
          —— 健康度按「无运行记录」如实展示,不伪装成数据为空。
        </div>
      ) : null}

      {outcome?.error ? <ErrorBox error={outcome.error} /> : null}
      {outcome?.check && !outcome.check.consistent ? (
        <div
          role="alert"
          className="mx-6 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          往返复核失败:{outcome.check.message}
        </div>
      ) : null}
      {outcome?.check?.consistent ? (
        <div
          role="status"
          className="mx-6 rounded-md border border-ok/30 bg-ok/10 px-4 py-3 text-sm text-ok"
          data-testid="roundtrip-ok"
        >
          已{outcome.next ? "启用" : "停用"} {outcome.sourceName} · doctor 复核往返一致
        </div>
      ) : null}

      {/* 试抓(C13)回显:进行中 spinner 文案 / 完成摘要;结果只驻屏内(如实注记) */}
      {testing ? (
        <div
          role="status"
          className="mx-6 flex items-center gap-2 rounded-md border border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground"
          data-testid="test-running"
        >
          <RefreshCw className="size-3.5 animate-spin" />
          试抓 {testing.sourceName} 进行中(异步 job #{testing.jobId},子进程日志见日志屏)…
        </div>
      ) : null}
      {testOutcome ? (
        <div
          role={testOutcome.ok ? "status" : "alert"}
          data-testid={`test-result-${testOutcome.ok ? "ok" : "fail"}`}
          className={
            testOutcome.ok
              ? "mx-6 rounded-md border border-ok/30 bg-ok/10 px-4 py-3 text-sm text-ok"
              : "mx-6 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
          }
        >
          试抓 {testOutcome.sourceName}:{testOutcome.summary}
        </div>
      ) : null}

      <div className="px-6">
        <Card>
          <CardContent className="flex flex-col gap-3 p-4">
            {state.status === "loading" ? (
              <div className="flex flex-col gap-2" aria-label="加载中">
                {[0, 1, 2].map((index) => (
                  <Skeleton key={index} className="h-9 w-full" />
                ))}
              </div>
            ) : state.status === "ready" && rows.length > 0 ? (
              <SourcesTable
                rows={rows}
                disabledKeys={disabledKeys}
                pendingKeys={pendingKeys}
                testingKey={testing?.key ?? null}
                onToggle={(row, next) => void handleToggle(row, next)}
                onEdit={(row) => setEditingFile(row.pluginFile)}
                onTest={(row) => void handleTest(row)}
              />
            ) : state.status === "ready" ? (
              <EmptyState
                title="还没有源数据"
                description={
                  state.data && state.data.summary.plugins > 0
                    ? "已装品类存在但都没有声明源;品类 YAML 的 sources: 节为空或加载失败"
                    : `插件目录(${state.data?.pluginsDir ?? "plugins"})下没有可加载的品类 YAML`
                }
              />
            ) : null}
          </CardContent>
        </Card>
      </div>

      {disabledEntries.length > 0 ? (
        <div className="px-6">
          <Card>
            <CardContent className="flex flex-col gap-2 p-4">
              <p className="text-sm font-medium text-foreground">本次会话已停用({disabledEntries.length})</p>
              <ul className="flex flex-col gap-1.5">
                {disabledEntries.map((entry) => (
                  <li key={entry.key} className="flex items-center justify-between gap-2 text-xs">
                    <span className="flex min-w-0 items-center gap-2">
                      <Badge variant="outline">已停用</Badge>
                      <span className="truncate font-medium text-foreground">{entry.name}</span>
                      <span className="truncate font-mono text-muted-foreground">{entry.file}</span>
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={pendingKeys.has(entry.key)}
                      onClick={() => {
                        const row = rows.find(
                          (candidate) =>
                            candidate.sourceName === entry.name && candidate.pluginFile === entry.file,
                        );
                        if (row) {
                          void handleToggle(row, true);
                        } else {
                          // 行已随刷新离开 health 列表:用轻量构造行发起启用
                          void handleToggle(
                            {
                              ...sourceRowShell,
                              pluginFile: entry.file,
                              sourceName: entry.name,
                            },
                            true,
                          );
                        }
                      }}
                    >
                      启用
                    </Button>
                  </li>
                ))}
              </ul>
              <p className="text-2xs text-muted-foreground">
                停用名单以品类 YAML 为准(写回应答为准出);会话前已停用的源不在 health 协议面,读取待协议扩展。
              </p>
            </CardContent>
          </Card>
        </div>
      ) : null}

      {/* 排程一览(G4,10-03-feed-ux):每品类 schedule/timezone 原文 + 未来 5 次
          (Apify 式 Next runs 预览,防 cron 写错);单品类失败只塌该行 */}
      {scheduleRows !== null ? (
        <div className="px-6" data-testid="schedule-overview">
          <Card>
            <CardContent className="flex flex-col gap-2 p-4">
              <p className="text-sm font-medium text-foreground">排程一览(未来 5 次)</p>
              {scheduleRows.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  插件目录下没有品类 YAML;先装品类再看排程。
                </p>
              ) : (
                <ul className="flex flex-col gap-1.5">
                  {scheduleRows.map((row) => (
                    <li
                      key={row.file}
                      data-testid={`schedule-row-${row.file}`}
                      className="flex flex-wrap items-center justify-between gap-2 border-b border-border/40 py-1.5 text-xs last:border-b-0"
                    >
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="truncate font-medium text-foreground">{row.name}</span>
                        {row.error ? (
                          <Badge variant="destructive" title={row.error}>
                            预览失败
                          </Badge>
                        ) : row.schedule ? (
                          <Badge variant="outline" className="font-mono" title="5 段 cron 原文">
                            {row.schedule}
                          </Badge>
                        ) : (
                          <Badge variant="unknown">无排程</Badge>
                        )}
                        {row.timezone ? (
                          <span className="font-mono text-muted-foreground">{row.timezone}</span>
                        ) : null}
                      </span>
                      <span className="flex items-center gap-1 font-mono text-muted-foreground">
                        {row.error ? (
                          <span className="truncate text-destructive" title={row.error}>
                            {row.error}
                          </span>
                        ) : row.runs.length > 0 ? (
                          row.runs.map((run) => (
                            <span
                              key={run}
                              className="rounded border border-border/60 bg-muted/30 px-1 py-0.5"
                              title={run}
                            >
                              {formatScheduleRun(run)}
                            </span>
                          ))
                        ) : (
                          <span>—</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="text-2xs text-muted-foreground">
                预览为纯计算(schedule.preview);排程执行属 CLI/常驻形态,改 schedule 节到「配置编辑」。
              </p>
            </CardContent>
          </Card>
        </div>
      ) : null}

      {/* 当场编辑弹窗:行「编辑」打开;关闭守卫(dirty confirm)在弹窗内部;保存成功刷新表格 */}
      {editingFile !== null ? (
        <YamlEditorDialog file={editingFile} onClose={() => setEditingFile(null)} onSaved={handleDialogSaved} />
      ) : null}
    </div>
  );
}

/** 启用按钮的兜底行(行不在表里时仍可发起 enable 写回;只带键位字段) */
const sourceRowShell: SourceRow = {
  pluginFile: "",
  pluginId: null,
  pluginName: null,
  pluginLoaded: true,
  pluginLoadErrors: null,
  pluginSchedule: null,
  sourceName: "",
  url: "",
  engine: "",
  engineHint: null,
  health: {
    state: "unknown",
    reason: "",
    observed: 0,
    latest: null,
    baseline: null,
  },
  fingerprintSkips: { observed: 0, skipped: 0 },
};
