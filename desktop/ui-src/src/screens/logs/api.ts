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
  RunStartResult,
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
  /** 重跑参数面(G7):run.start 需要的 yaml/dry/db 三件,原样回放该 run */
  yaml: string;
  db: string;
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
    yaml: run.yaml,
    db: run.db,
    dry: run.dry,
    durationText: formatDuration(run.duration_ms),
    itemCount: itemsRetained(run.record),
  }));
}

export async function loadRuns(): Promise<RunEntry[]> {
  return (await api.runStatus()).runs;
}

/**
 * 重跑(G7):骑 G4 手动触发通道 run.start,不新增协议方法——把该 run 的
 * yaml/dry/db 原样回放(dry run 重跑仍是 dry);成功返回新 run_id,终态与
 * 日志经既有 completed 事件 + run.status 列表刷新可见。
 */
export function rerunRun(run: Pick<RunRowModel, "yaml" | "dry" | "db">): Promise<RunStartResult> {
  return api.runStart({ yaml: run.yaml, dry: run.dry, db: run.db });
}

// ---------------------------------------------------------------------------
// 过滤与搜索(G7 纯函数,供组件与测试直接消费)
// ---------------------------------------------------------------------------

/** 状态过滤键:"running" 活跃态 + 五档终态(与 runBadge 同口径) */
export type RunFilterStatus = "running" | RunExitStatus;

/** run → 状态过滤键:state=running(或 status 未知)归 running,其余按终态 */
export function runFilterStatus(run: Pick<RunRowModel, "status" | "state">): RunFilterStatus {
  if (run.state === "running" || run.status === null) return "running";
  return run.status;
}

/** 品类/状态双过滤:"all" 为通配(单选项直选,不做多选) */
export function filterRunRows(
  rows: RunRowModel[],
  category: string,
  status: RunFilterStatus | "all",
): RunRowModel[] {
  return rows.filter(
    (row) =>
      (category === "all" || row.category === category) &&
      (status === "all" || runFilterStatus(row) === status),
  );
}

/** 搜索命中(大小写不敏感子串);needle 已 trim+lower,空串通配一切行 */
export function rowMatchesQuery(row: LogRow, needle: string): boolean {
  return needle === "" || row.text.toLowerCase().includes(needle);
}

/**
 * 搜索高亮切分:把 text 按 needle(已 trim+lower)切成片段序列,命中片段
 * hit=true。手写 indexOf 循环而非 RegExp——免正则元字符转义坑,任意输入安全。
 */
export function splitTextOnQuery(text: string, needle: string): Array<{ text: string; hit: boolean }> {
  if (needle === "") return [{ text, hit: false }];
  const parts: Array<{ text: string; hit: boolean }> = [];
  const haystack = text.toLowerCase();
  let cursor = 0;
  let index = haystack.indexOf(needle);
  while (index !== -1) {
    if (index > cursor) parts.push({ text: text.slice(cursor, index), hit: false });
    parts.push({ text: text.slice(index, index + needle.length), hit: true });
    cursor = index + needle.length;
    index = haystack.indexOf(needle, cursor);
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), hit: false });
  return parts;
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
  if (event.type === "completed") {
    return { key: `event:${seq}`, runId: event.run_id, stream: "system", text: completedText(event), ts: event.ts };
  }
  if (event.type === "test.completed") {
    // 试抓 job 结果(C13,v1.1.2 桌面对齐批):一行系统摘要(run_id=null,
    // 与 sources.test 的环形缓冲 run_id=null 同口径;详情在源管理屏回显)
    const outcome = event.ok
      ? `▸ 试抓完成(job #${event.job_id})`
      : `▸ 试抓失败(job #${event.job_id}:${event.error ?? "error"})`;
    return { key: `event:${seq}`, runId: null, stream: "system", text: outcome, ts: event.ts };
  }
  if (event.type === "image.models.progress") {
    // 模型下载进度(10-03-vision-v2):一行系统摘要(run_id=null,下载 job 不挂
    // run;后端 0.5s 节流;详情进度条在设置屏模型管理卡)
    const total = event.total_bytes === undefined ? "总量未知" : `${event.total_bytes}B`;
    return {
      key: `event:${seq}`,
      runId: null,
      stream: "system",
      text: `▸ 模型下载进度(job #${event.job_id} ${event.repo}:${event.done_bytes}B / ${total})`,
      ts: event.ts,
    };
  }
  if (event.type === "image.models.completed") {
    const outcome = event.ok
      ? `▸ 模型下载完成(job #${event.job_id})`
      : `▸ 模型下载失败(job #${event.job_id}:${event.error ?? "error"})`;
    return { key: `event:${seq}`, runId: null, stream: "system", text: outcome, ts: event.ts };
  }
  if (event.type === "image.server.completed") {
    // server ensure 终态(10-03-vision-v2 复查:ensure 慢路径应答即返,终态走
    // 本事件):一行系统摘要;徽章翻正在设置屏模型管理卡
    const outcome = event.ok
      ? `▸ 本地视觉服务已就绪(job #${event.job_id})`
      : `▸ 本地视觉服务确保启动失败(job #${event.job_id}:${event.error ?? "error"})`;
    return { key: `event:${seq}`, runId: null, stream: "system", text: outcome, ts: event.ts };
  }
  // 穷尽防御:SidecarEvent = log/progress/completed/test.completed +
  // image.models.progress/completed + image.server.completed 七种
  // (10-03-vision-v2 增模型下载域两事件 + server ensure 终态事件);
  // 协议再添类型时此处编译期即报错
  const unknownEvent: never = event;
  return { key: `event:${seq}`, runId: null, stream: "system", text: `▸ 未识别事件(${String(unknownEvent)})`, ts: "" };
}

// ---------------------------------------------------------------------------
// 行级着色判定:Crawlab 级别着色思路(BSD-3 可直借,licenses.md 实核)——
// 错误关键词 = 错误(dead);WARNING 级/裸 stderr = 警示;INFO/DEBUG = 正常
// ---------------------------------------------------------------------------

/** stderr 结构化日志里的进度行(phase 信号源)不算错误,白名单放行 */
const PROGRESS_INFIX = /运行开始|采集完成|采集步骤完成|运行结束/;

const ERROR_PATTERN = /\b(error|fatal|traceback|exception|panic|failed)\b|失败|错误|异常/i;

export function isRowError(row: LogRow): boolean {
  if (row.stream === "system") return false;
  if (PROGRESS_INFIX.test(row.text)) return false;
  return ERROR_PATTERN.test(row.text);
}

/** 结构化日志级别段(python logging 行「… INFO myia.pipeline: …」同款) */
const LEVEL_WARN = /(?:^|\s)(?:WARNING|WARN)\b/;
const LEVEL_INFO = /(?:^|\s)(?:INFO|DEBUG)\b/i;

/**
 * 警示行:WARNING/WARN 级 → 警示;带 INFO/DEBUG 级标的 stderr 行是正常运行
 * 日志(本仓采集管线日志全走 stderr),不着警示色;无级标的裸 stderr(非结构
 * 化诊断直吐)保守按警示。
 */
export function isRowWarn(row: LogRow): boolean {
  if (isRowError(row)) return false;
  if (row.stream !== "stderr") return false;
  if (LEVEL_WARN.test(row.text)) return true;
  return !LEVEL_INFO.test(row.text);
}
