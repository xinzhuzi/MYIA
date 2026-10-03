/**
 * 仪表盘数据装配(本屏私有 api 模块;共享客户端 @/lib/api 只读不动)。
 *
 * 数据面 = sidecar 协议三方法(entry.py `_HANDLERS`):
 *   doctor     → myia doctor --json 等价(品类/源健康度/findings);
 *   runs.list  → runs 表直读(新→旧;重启 .app 后历史仍可达,C3);
 *   run.status → 内存注册表(活跃 run 叠加;进行中 run 的实时态)。
 * 纯函数聚合出三块视图模型:品类状态卡 / 源健康度汇总 / 近期 run 成功率。
 * 结构化错误不在此吞:SidecarRequestError 原样上抛,由组件渲染 code/message。
 */
import { api } from "@/lib/api";
import type {
  DoctorPluginReport,
  DoctorResult,
  Finding,
  RunEntry,
  RunRecord,
  SourceHealthState,
  StoreTrendResult,
  TrendDay,
} from "@/lib/api";

/**
 * 仪表盘 run 行视图模型:历史行(runs.list)+ 活跃叠加(run.status 内存态)。
 * active = 当前会话内进行中(run.status state=running);history 行里
 * status="running" 但非 active = sidecar 中断遗留的僵尸行(如实标「中断」)。
 */
export interface DashboardRun {
  runId: number;
  /** 品类 id;活跃 dry run 无表行,取 yaml 文件名 */
  category: string;
  /** runs 表 status(success/partial/failed/config_error/running/…;活跃且无表行 = null) */
  status: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  stats: Record<string, unknown> | null;
  active: boolean;
  dry: boolean;
  /** 毫秒;历史行由 finishedAt-startedAt 推导,活跃行取注册表实时值 */
  durationMs: number | null;
}

/** 一次仪表盘刷新的原始快照 */
export interface DashboardData {
  doctor: DoctorResult;
  /** 合并后的 run 行(新→旧) */
  runs: DashboardRun[];
}

/** 历史 20 条 + 内存活跃叠加;并发拉三方法,任一失败即整体拒绝(错误上抛)。 */
export async function loadDashboardData(): Promise<DashboardData> {
  const [doctor, history, registry] = await Promise.all([
    api.doctor(),
    api.runsList({ limit: 20 }),
    api.runStatus(),
  ]);
  return { doctor, runs: mergeDashboardRuns(history.runs, registry.runs) };
}

/**
 * 合并 runs.list 历史行与 run.status 内存活跃条目:
 * ① 活跃 run 若已写表(pipeline start_run 即写,status="running"),给最新
 *   running 行打 active 并带上注册表实时 duration;
 * ② 活跃 run 无表行(dry run 不落库/表行写入滞后)→ 前插合成行;
 * ③ 其余 history 行原样;表内 status="running" 且无内存活跃 = 中断遗留。
 */
export function mergeDashboardRuns(history: RunRecord[], registry: RunEntry[]): DashboardRun[] {
  const actives = registry.filter((entry) => entry.state === "running");
  const rows: DashboardRun[] = history.map((record) => ({
    runId: record.run_id,
    category: record.category,
    status: record.status,
    startedAt: record.started_at,
    finishedAt: record.finished_at,
    stats: record.stats,
    active: false,
    dry: false,
    durationMs: elapsedMs(record.started_at, record.finished_at),
  }));
  for (const active of actives) {
    const tableRow = rows.find((row) => !row.active && row.status === "running");
    if (tableRow) {
      tableRow.active = true;
      tableRow.dry = active.dry;
      tableRow.durationMs = active.duration_ms ?? tableRow.durationMs;
    } else {
      rows.unshift({
        runId: active.run_id,
        category: active.yaml.split("/").pop() ?? active.yaml,
        status: null,
        startedAt: active.started_at,
        finishedAt: null,
        stats: null,
        active: true,
        dry: active.dry,
        durationMs: active.duration_ms,
      });
    }
  }
  return rows;
}

/** ISO 对差值(毫秒);任一缺失/不可解析 = null(不虚构时长)。 */
export function elapsedMs(startedAt: string | null, finishedAt: string | null): number | null {
  if (!startedAt || !finishedAt) return null;
  const start = Date.parse(startedAt);
  const end = Date.parse(finishedAt);
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  return end - start;
}

// ---------------------------------------------------------------------------
// 源健康度汇总(ok / degraded / dead / unknown 四态计数)
// ---------------------------------------------------------------------------

export type SourceHealthCounts = Record<SourceHealthState, number>;

export function emptyHealthCounts(): SourceHealthCounts {
  return { ok: 0, degraded: 0, dead: 0, unknown: 0 };
}

/** 遍历 doctor.plugins[].sources[].health.state 计数(与 health.summary 同口径) */
export function summarizeSourceHealth(doctor: DoctorResult): SourceHealthCounts {
  const counts = emptyHealthCounts();
  for (const plugin of doctor.plugins) {
    for (const source of plugin.sources) {
      counts[source.health.state] += 1;
    }
  }
  return counts;
}

// ---------------------------------------------------------------------------
// 近期 run 成功率(state=done 的 run 中 status==="success" 占比)
// ---------------------------------------------------------------------------

export interface RunSuccessSummary {
  total: number;
  running: number;
  finished: number;
  /** status==="success" 的完成 run 数(exit_code 0 语义,见 types.ts RunRecord.status) */
  success: number;
  /** success / finished;无完成 run 时 null(不虚构成 0%) */
  successRate: number | null;
  /** 最近 N 个 run(保持新→旧原序) */
  recent: DashboardRun[];
}

/** 近期列表长度(仪表盘只展示最近 10 条) */
export const RECENT_RUNS_COUNT = 10;

/**
 * 成功率聚合(吃 runs.list 合并行):active = 运行中不计入;其余全算已完结
 * (含中断遗留的 status="running" 僵尸行 —— 拖低成功率是如实的)。
 */
export function summarizeRuns(runs: DashboardRun[], recentCount = RECENT_RUNS_COUNT): RunSuccessSummary {
  const activeRuns = runs.filter((run) => run.active);
  const finishedRuns = runs.filter((run) => !run.active);
  const success = finishedRuns.filter((run) => run.status === "success").length;
  return {
    total: runs.length,
    running: activeRuns.length,
    finished: finishedRuns.length,
    success,
    successRate: finishedRuns.length > 0 ? success / finishedRuns.length : null,
    recent: runs.slice(0, recentCount),
  };
}

/** 0.8 → "80%";null → "—"(无完成 run) */
export function formatSuccessRate(rate: number | null): string {
  if (rate === null) return "—";
  return `${Math.round(rate * 100)}%`;
}

// ---------------------------------------------------------------------------
// 品类状态卡(doctor.plugins 一品类一卡;findings 按 scope 归属回品类)
// ---------------------------------------------------------------------------

export type CategoryTone = "ok" | "warning" | "dead";

export interface CategoryCardModel {
  /** 品类 YAML 文件名(doctor findings 的 scope 锚点) */
  file: string;
  name: string;
  loaded: boolean;
  schedule: string | null;
  /** 调度下次触发时间(ISO;计算失败为 null) */
  nextFireAt: string | null;
  sourceCount: number;
  errorCount: number;
  warningCount: number;
  tone: CategoryTone;
}

/**
 * 归属本品类的 findings:cli.py `_plugin_findings` 的 scope 形态为
 * `plugin:<file>` 与 `plugin:<file>/source:<name>`,前缀匹配即可。
 */
export function pluginFindings(doctor: DoctorResult, file: string): Finding[] {
  const prefix = `plugin:${file}`;
  return doctor.findings.filter(
    (finding) => finding.scope === prefix || finding.scope.startsWith(`${prefix}/`),
  );
}

export function buildCategoryCards(doctor: DoctorResult): CategoryCardModel[] {
  return doctor.plugins.map((plugin: DoctorPluginReport) => {
    const findings = pluginFindings(doctor, plugin.file);
    const errorCount = findings.filter((finding) => finding.severity === "error").length;
    const warningCount = findings.filter((finding) => finding.severity === "warning").length;
    const tone: CategoryTone =
      !plugin.loaded || errorCount > 0 ? "dead" : warningCount > 0 ? "warning" : "ok";
    return {
      file: plugin.file,
      name: plugin.name ?? plugin.file,
      loaded: plugin.loaded,
      schedule: plugin.schedule,
      nextFireAt: plugin.next_fire_at,
      sourceCount: plugin.sources.length,
      errorCount,
      warningCount,
      tone,
    };
  });
}

// ---------------------------------------------------------------------------
// 采集量趋势(B4,10-03-v112-desktop-parity:store.trend 纯函数装配)
// ---------------------------------------------------------------------------

/** 趋势窗口档位(卡头切换;服务端钳制 [1,90]) */
export const TREND_WINDOW_DAYS = [7, 14, 30] as const;
export type TrendWindowDays = (typeof TREND_WINDOW_DAYS)[number];
export const TREND_WINDOW_DEFAULT: TrendWindowDays = 14;

/** UTC「今天」的 YYYY-MM-DD(趋势窗口右端;口径 = UTC 逐日,卡面如实注记)。 */
export function utcToday(): string {
  return new Date().toISOString().slice(0, 10);
}

/** UTC 日期串加减天数(纯字符串日历运算,不经本地时区)。 */
export function shiftUtcDate(date: string, deltaDays: number): string {
  const ms = Date.parse(`${date}T00:00:00Z`);
  if (Number.isNaN(ms)) return date; // 防御:非法入参原样返回,调用侧对齐失败可见
  return new Date(ms + deltaDays * 86_400_000).toISOString().slice(0, 10);
}

/**
 * 补零对齐:把 store.trend 的稀疏逐日计数铺满「截至 today 的 days 天窗口」——
 * 缺数日补 0、窗口外行丢弃、旧→新稳定输出(空态 = 全零窗口,不是空数组:
 * sparkline 需要等长序列)。today 显式传入(纯函数可测)。
 */
export function fillDailyCounts(rows: TrendDay[], days: number, today: string): TrendDay[] {
  if (!Number.isInteger(days) || days <= 0) return [];
  const byDate = new Map<string, number>();
  for (const row of rows) {
    if (/^\d{4}-\d{2}-\d{2}$/.test(row.date)) byDate.set(row.date, row.count);
  }
  const out: TrendDay[] = [];
  for (let offset = days - 1; offset >= 0; offset -= 1) {
    const date = shiftUtcDate(today, -offset);
    out.push({ date, count: byDate.get(date) ?? 0 });
  }
  return out;
}

/** SVG polyline 坐标:等距 x + 按 max 归一 y(全零 = 居中平线,不除零)。 */
export function toSparklinePoints(
  counts: number[],
  width: number,
  height: number,
  pad = 3,
): string {
  if (counts.length === 0 || width <= pad * 2 || height <= pad * 2) return "";
  const max = Math.max(...counts, 0);
  const spanX = width - pad * 2;
  const spanY = height - pad * 2;
  return counts
    .map((count, index) => {
      const x = counts.length === 1 ? width / 2 : pad + (spanX * index) / (counts.length - 1);
      const y = max === 0 ? height / 2 : pad + spanY * (1 - count / max);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

/** 趋势行 → sparkline 数据(拉平 count;补零由 fillDailyCounts 负责)。 */
export function trendCounts(filled: TrendDay[]): number[] {
  return filled.map((day) => day.count);
}

/** store.trend 应答 → 补零窗口(独立小装配,卡组件直用)。 */
export function toTrendWindow(result: StoreTrendResult, days: number, today: string): TrendDay[] {
  return fillDailyCounts(result.days, days, today);
}

// ---------------------------------------------------------------------------
// 小格式化(本屏私有;跨屏抽取属共享层,不在本任务边界)
// ---------------------------------------------------------------------------

/** 毫秒 → "830ms" / "1.2s" / "2m3s";null/非法 → "—" */
export function formatDuration(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m${Math.round(seconds % 60)}s`;
}

/** run 记录 stats 的条目数(pipeline.py `stats_dict` 的 items_retained) */
export function runItemCount(run: DashboardRun): number | null {
  const raw = run.stats?.["items_retained"];
  return typeof raw === "number" ? raw : null;
}
