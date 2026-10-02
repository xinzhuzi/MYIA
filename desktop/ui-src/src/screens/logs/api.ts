/**
 * 采集日志数据装配(本屏私有 api 模块;共享客户端 @/lib/api 只读不动)。
 *
 * 数据面 = sidecar 协议三通道:
 *   run.status    → run 列表(新→旧;状态/耗时/条目数,entry.py `_m_run_status`);
 *   logs.tail     → 环形缓冲历史(可按 run_id 过滤,`_m_logs_tail`);
 *   sidecar://event → log/progress/completed 流式事件(壳转发,`_pump_stream`)。
 * 流式渲染 = tail 打底 + 事件续播;错误行按 stderr 通道 + 关键词双信号高亮。
 */
import { api, onSidecarEvent } from "@/lib/api";
import type {
  LogLine,
  LogsTailResult,
  ProgressEvent,
  RunEntry,
  RunExitStatus,
  RunRecord,
  SidecarEvent,
  UnlistenFn,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// run 列表
// ---------------------------------------------------------------------------

export interface RunRowModel {
  runId: number;
  /** 品类名:record.category 优先,缺省取 yaml 文件名(去目录与扩展名) */
  category: string;
  status: RunExitStatus | null;
  state: RunEntry["state"];
  dry: boolean;
  durationText: string;
  /** record.stats.items_retained(pipeline.py `stats_dict`);无记录为 null */
  itemCount: number | null;
}

/** 毫秒 → "830ms" / "1.2s" / "2m3s";null/非法 → "—"(本屏私有格式化) */
export function formatDuration(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m${Math.round(seconds % 60)}s`;
}

export function runCategory(run: RunEntry): string {
  if (run.record?.category) return run.record.category;
  const file = run.yaml.split(/[\\/]/).pop() ?? run.yaml;
  return file.replace(/\.ya?ml$/i, "");
}

function itemsRetained(record: RunRecord | null): number | null {
  const raw = record?.stats?.["items_retained"];
  return typeof raw === "number" ? raw : null;
}

export function buildRunRows(runs: RunEntry[]): RunRowModel[] {
  return runs.map((run) => ({
    runId: run.run_id,
    category: runCategory(run),
    status: run.status,
    state: run.state,
    dry: run.dry,
    durationText: formatDuration(run.duration_ms),
    itemCount: itemsRetained(run.record),
  }));
}

export async function loadRuns(): Promise<RunEntry[]> {
  return (await api.runStatus()).runs;
}

/** run_id 为 null = 不过滤(全 run 的最近日志) */
export async function loadRunLogs(runId: number | null, lines = 400): Promise<LogsTailResult> {
  return api.logsTail(runId === null ? { lines } : { lines, run_id: runId });
}

/** 订阅壳转发的 sidecar 事件流;返回取消订阅函数。 */
export function subscribeRunEvents(handler: (event: SidecarEvent) => void): Promise<UnlistenFn> {
  return onSidecarEvent(handler);
}

// ---------------------------------------------------------------------------
// 日志行视图模型:tail 历史打底 + 事件续播
// ---------------------------------------------------------------------------

export interface LogRow {
  /** 渲染 key:tail 行用 seq,事件行用自增序号(前缀区分防碰撞) */
  key: string;
  runId: number | null;
  /** system = 进度/完成事件的合成行(非子进程原行) */
  stream: "stdout" | "stderr" | "system";
  text: string;
  ts: string;
}

export function tailToRows(result: LogsTailResult): LogRow[] {
  return result.lines.map((line: LogLine) => ({
    key: `seq:${line.seq}`,
    runId: line.run_id,
    stream: line.stream,
    text: line.line,
    ts: line.ts,
  }));
}

const PROGRESS_LABEL: Record<ProgressEvent["phase"], string> = {
  run_start: "运行开始",
  source_done: "源完成",
  fetch_done: "采集步骤完成",
  run_end: "运行结束",
};

function progressText(event: ProgressEvent): string {
  const fields: string[] = [];
  if (event.category !== undefined) fields.push(`category=${event.category}`);
  if (event.source !== undefined) fields.push(`source=${event.source}`);
  if (event.engine !== undefined) fields.push(`engine=${event.engine}`);
  if (event.items !== undefined) fields.push(`items=${event.items}`);
  if (event.sources !== undefined) fields.push(`sources=${event.sources}`);
  if (event.source_failures !== undefined) fields.push(`source_failures=${event.source_failures}`);
  if (event.run_status !== undefined) fields.push(`status=${event.run_status}`);
  const joined = fields.length > 0 ? ` ${fields.join(" ")}` : "";
  return `▸ ${PROGRESS_LABEL[event.phase] ?? event.phase}${joined}`;
}

function completedText(event: SidecarEvent & { type: "completed" }): string {
  if (event.error !== undefined) {
    return `● run ${event.run_id} 异常终止:${event.error}`;
  }
  const status = event.status ?? "未知";
  const exit = event.exit_code === null ? "—" : String(event.exit_code);
  const duration = formatDuration(event.duration_ms ?? null);
  return `● run ${event.run_id} 结束 · status=${status} · exit=${exit} · 耗时 ${duration}`;
}

/** 事件 → 日志行;seq 由调用方自增(保证 key 稳定且不与 tail seq 碰撞) */
export function eventToRow(event: SidecarEvent, seq: number): LogRow {
  if (event.type === "log") {
    return { key: `event:${seq}`, runId: event.run_id, stream: event.stream, text: event.line, ts: event.ts };
  }
  if (event.type === "progress") {
    return { key: `event:${seq}`, runId: event.run_id, stream: "system", text: progressText(event), ts: event.ts };
  }
  return { key: `event:${seq}`, runId: event.run_id, stream: "system", text: completedText(event), ts: event.ts };
}

// ---------------------------------------------------------------------------
// 错误行高亮判定:stderr 通道 = 警示;错误关键词 = 错误(红)
// ---------------------------------------------------------------------------

/** stderr 结构化日志里的进度行(phase 信号源)不算错误,白名单放行 */
const PROGRESS_INFIX = /运行开始|采集完成|采集步骤完成|运行结束/;

const ERROR_PATTERN = /\b(error|fatal|traceback|exception|panic|failed)\b|失败|错误|异常/i;

export function isRowError(row: LogRow): boolean {
  if (row.stream === "system") return false;
  if (PROGRESS_INFIX.test(row.text)) return false;
  return ERROR_PATTERN.test(row.text);
}

export function isRowWarn(row: LogRow): boolean {
  return !isRowError(row) && row.stream === "stderr";
}
