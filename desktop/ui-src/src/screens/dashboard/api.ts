/**
 * 仪表盘数据装配(本屏私有 api 模块;共享客户端 @/lib/api 只读不动)。
 *
 * 数据面 = sidecar 协议两方法(entry.py `_HANDLERS`):
 *   doctor     → myia doctor --json 等价(品类/源健康度/findings);
 *   run.status → run 注册表(新→旧,state/exit_code/status/duration_ms)。
 * 纯函数聚合出三块视图模型:品类状态卡 / 源健康度汇总 / 近期 run 成功率。
 * 结构化错误不在此吞:SidecarRequestError 原样上抛,由组件渲染 code/message。
 */
import { api } from "@/lib/api";
import type {
  DoctorPluginReport,
  DoctorResult,
  Finding,
  RunEntry,
  SourceHealthState,
} from "@/lib/api";

/** 一次仪表盘刷新的原始快照 */
export interface DashboardData {
  doctor: DoctorResult;
  /** run 注册表(新→旧,见 entry.py `_m_run_status`) */
  runs: RunEntry[];
}

/** 并发拉 doctor + runs;任一失败即整体拒绝(结构化错误上抛)。 */
export async function loadDashboardData(): Promise<DashboardData> {
  const [doctor, runStatus] = await Promise.all([api.doctor(), api.runStatus()]);
  return { doctor, runs: runStatus.runs };
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
  /** status==="success" 的完成 run 数(exit_code 0 语义,见 types.ts RunExitStatus) */
  success: number;
  /** success / finished;无完成 run 时 null(不虚构成 0%) */
  successRate: number | null;
  /** 最近 N 个 run(保持新→旧原序) */
  recent: RunEntry[];
}

/** 近期列表长度(仪表盘只展示最近 10 条) */
export const RECENT_RUNS_COUNT = 10;

export function summarizeRuns(runs: RunEntry[], recentCount = RECENT_RUNS_COUNT): RunSuccessSummary {
  const finishedRuns = runs.filter((run) => run.state === "done");
  const success = finishedRuns.filter((run) => run.status === "success").length;
  return {
    total: runs.length,
    running: runs.length - finishedRuns.length,
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
export function runItemCount(run: RunEntry): number | null {
  const raw = run.record?.stats?.["items_retained"];
  return typeof raw === "number" ? raw : null;
}
