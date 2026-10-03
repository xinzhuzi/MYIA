/**
 * 源管理数据装配(本屏私有 api 模块)。
 *
 * 数据面 = sidecar 协议既有方法(entry.py `_HANDLERS`,注册表见
 * .trellis/spec/desktop/sidecar-protocol.md):
 *   health → myia list --json 等价:插件清单 + 源健康度(ok/degraded/dead/unknown)
 *            + summary 聚合;doctor → myia doctor --json 等价(结构化 findings)。
 *
 * 启停写回(`sources.write`,已收编进 entry.py `_HANDLERS`):本模块保留
 * 屏私有 invoke 通道(spec 变更纪律第 3 条「封装面 ≠ 协议面」:sources.write
 * 不入共享 client 门面;错误归一化与共享 client 同规则)。契约:
 *
 *   请求  {"method": "sources.write", "params": {"file", "enable"?, "disable"?}}
 *   应答  {"file", "written": true, "enabled": string[], "disabled": string[]}
 *   语义  file = health 报告里的品类 YAML 路径原样回传;disable 把源从
 *         sources: 摘出(lossless 暂存:顶层 disabled_sources: 节,文本手术
 *         注释逐字节保真);enable 移回。
 *   往返一致 = 写回后 doctor({yamls:[file]}) 报告的 sources 名单与应答
 *         enabled 完全一致(见 verifySourceRoundTrip)。
 *
 * 试抓此源(C13,10-03-v112-desktop-parity):发起走共享门面 `api.sourcesTest`
 * (本批新方法全入共享 client,spec 注册表新增行 ↔ 门面新增行同源对账);
 * 结果经 `test.completed` 事件回屏,摘要在 summarizeTestCompleted。
 */
import { invoke } from "@tauri-apps/api/core";

import { api, SidecarRequestError } from "@/lib/api";
import type {
  FingerprintSkips,
  HealthSummary,
  LoadErrorDetail,
  PluginReport,
  SidecarErrorShape,
  SourceHealth,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// 视图模型:health 两层(插件/源)扁平成表格行
// ---------------------------------------------------------------------------

/** 表格一行 = 品类插件 × 源;health 只报 sources: 里启用的源,故 enabled 恒真 */
export interface SourceRow {
  /** 品类 YAML 路径(health PluginReport.file 原样;启停写回的目标) */
  pluginFile: string;
  pluginId: string | null;
  pluginName: string | null;
  pluginLoaded: boolean;
  /** 插件加载失败明细(loaded=false 时非空;表格里以徽标+悬浮提示呈现) */
  pluginLoadErrors: LoadErrorDetail[] | null;
  /** 品类调度周期(展示用;health PluginReport.schedule) */
  pluginSchedule: string | null;
  sourceName: string;
  url: string;
  engine: string;
  engineHint: string | null;
  health: SourceHealth;
  fingerprintSkips: FingerprintSkips;
}

export interface SourcesData {
  rows: SourceRow[];
  summary: HealthSummary;
  /** health 的 plugins_dir / db(空态文案里交代数据从哪来) */
  pluginsDir: string;
  db: string;
  /** store 打不开时的结构化明细(如实展示,不伪装成「暂无数据」) */
  storeError: { error_type: string; message: string } | null;
  /** health 的插件原序清单(排程一览按品类枚举;无源品类也在内) */
  plugins: PluginReport[];
}

/** 扁平化 PluginReport[] → SourceRow[];插件保持原序,源保持 YAML 声明序。 */
export function flattenHealthPlugins(plugins: PluginReport[]): SourceRow[] {
  const rows: SourceRow[] = [];
  for (const plugin of plugins) {
    for (const source of plugin.sources) {
      rows.push({
        pluginFile: plugin.file,
        pluginId: plugin.id,
        pluginName: plugin.name,
        pluginLoaded: plugin.loaded,
        pluginLoadErrors: plugin.load_errors,
        pluginSchedule: plugin.schedule,
        sourceName: source.name,
        url: source.url,
        engine: source.engine,
        engineHint: source.engine_hint,
        health: source.health,
        fingerprintSkips: source.fingerprint_skips,
      });
    }
  }
  return rows;
}

/** 并发语义上单方法即可;store 打不开时 health 仍返回(list 契约),原样透传。 */
export async function loadSourcesData(): Promise<SourcesData> {
  const health = await api.health();
  return {
    rows: flattenHealthPlugins(health.plugins),
    summary: health.summary,
    pluginsDir: health.plugins_dir,
    db: health.db,
    storeError: health.store_error,
    plugins: health.plugins,
  };
}

// ---------------------------------------------------------------------------
// 排程一览(G4,10-03-feed-ux):schedule.preview 逐品类并发,单品类失败不塌整区
// ---------------------------------------------------------------------------

/** 排程一览一行:品类 + schedule/timezone 原文 + 未来 5 次预览 */
export interface ScheduleRow {
  /** 品类 YAML 路径(health PluginReport.file) */
  file: string;
  /** 品类展示名(name ?? 文件名) */
  name: string;
  schedule: string | null;
  timezone: string | null;
  /** 未来运行时刻(ISO 原文;界面本地化渲染) */
  runs: string[];
  /** 单品类预览失败(结构化 code:message;不塌整区) */
  error: string | null;
}

/** 逐品类 schedule.preview(Promise.allSettled 并发;count=5 与设计一致) */
export async function loadScheduleRows(plugins: PluginReport[], count = 5): Promise<ScheduleRow[]> {
  const settled = await Promise.allSettled(
    plugins.map((plugin) => api.schedulePreview({ file: plugin.file, count })),
  );
  return plugins.map((plugin, index) => {
    const outcome = settled[index];
    if (outcome.status === "rejected") {
      const failure =
        outcome.reason instanceof SidecarRequestError
          ? `${outcome.reason.code}: ${outcome.reason.message}`
          : String(outcome.reason);
      return {
        file: plugin.file,
        name: plugin.name ?? plugin.file,
        schedule: plugin.schedule,
        timezone: plugin.timezone,
        runs: [],
        error: failure,
      };
    }
    return {
      file: plugin.file,
      name: plugin.name ?? plugin.file,
      schedule: outcome.value.schedule,
      timezone: outcome.value.timezone,
      runs: outcome.value.runs,
      error: null,
    };
  });
}

/** ISO 时刻 → 本地「MM-DD HH:mm」短行(排程预览的紧凑呈现) */
export function formatScheduleRun(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return iso;
  const pad = (value: number) => String(value).padStart(2, "0");
  const sameYear = then.getFullYear() === now.getFullYear();
  const date = sameYear
    ? `${pad(then.getMonth() + 1)}-${pad(then.getDate())}`
    : `${then.getFullYear()}-${pad(then.getMonth() + 1)}-${pad(then.getDate())}`;
  return `${date} ${pad(then.getHours())}:${pad(then.getMinutes())}`;
}

// ---------------------------------------------------------------------------
// 启停写回:协议扩展提案 sources.write(契约见模块头注释)
// ---------------------------------------------------------------------------

export interface SourcesWriteParams {
  file: string;
  enable?: string[];
  disable?: string[];
}

export interface SourcesWriteResult {
  file: string;
  written: true;
  enabled: string[];
  disabled: string[];
}

/** 任意抛出物 → SidecarRequestError(与共享 client.toSidecarError 同规则;此处独立实现避免动共享层)。 */
export function asSidecarError(raw: unknown): SidecarRequestError {
  if (raw instanceof SidecarRequestError) return raw;
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw) as Partial<SidecarErrorShape>;
      if (parsed && typeof parsed.code === "string" && typeof parsed.message === "string") {
        return new SidecarRequestError({
          code: parsed.code,
          path: typeof parsed.path === "string" ? parsed.path : "$",
          message: parsed.message,
          data: parsed.data,
        });
      }
    } catch {
      // 非 JSON 文本:按裸消息包装
    }
    return new SidecarRequestError({ code: "transport_error", path: "$", message: raw });
  }
  return new SidecarRequestError({
    code: "sidecar_unavailable",
    path: "$",
    message: raw instanceof Error ? `Tauri IPC 不可用: ${raw.message}` : `Tauri IPC 不可用: ${String(raw)}`,
  });
}

/**
 * 启停写回:经壳命令 sidecar_request 调 `sources.write`(已收编;屏私有
 * invoke 通道保留,spec 变更纪律第 3 条)。
 */
export async function writeSourceToggle(params: SourcesWriteParams): Promise<SourcesWriteResult> {
  try {
    return await invoke<SourcesWriteResult>("sidecar_request", {
      method: "sources.write",
      params,
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

// ---------------------------------------------------------------------------
// 试抓此源(C13):sources.test 异步 job;结果摘要自 test.completed 事件
// ---------------------------------------------------------------------------

/** test.completed 的行内回显摘要(源/引擎/条数/指纹判定或失败原因) */
export interface TestOutcomeView {
  sourceName: string;
  ok: boolean;
  /** 一行摘要(ok 与失败两形;失败带 error 族与细节) */
  summary: string;
}

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null;
}

/**
 * test.completed 事件 → 行内摘要(ok 形:引擎 + 条数 + 指纹判定含义,取
 * result.sources[] 里本源的报告;失败形:CLI error 族 + 首条失败原因)。
 */
export function summarizeTestCompleted(event: {
  ok: boolean;
  error?: string;
  result?: Record<string, unknown>;
  data?: Record<string, unknown>;
}, sourceName: string): TestOutcomeView {
  if (!event.ok) {
    const detail = event.data && isRecord(event.data) && Array.isArray(event.data.errors)
      ? (() => {
          const first = event.data.errors[0];
          return isRecord(first) && typeof first.message === "string" ? first.message : "";
        })()
      : "";
    return {
      sourceName,
      ok: false,
      summary: `试抓失败(${event.error ?? "error"})${detail ? `:${detail}` : ""}`,
    };
  }
  const result = event.result ?? {};
  const reports = Array.isArray(result.sources) ? (result.sources as unknown[]) : [];
  const report = reports.find(
    (entry) => isRecord(entry) && entry.source === sourceName,
  );
  if (!isRecord(report)) {
    return { sourceName, ok: false, summary: "试抓完成但报告里没有该源的结果" };
  }
  const engine = typeof report.engine === "string" ? report.engine : String(report.engine_configured ?? "?");
  const count = typeof report.item_count === "number" ? report.item_count : 0;
  const fingerprint = isRecord(report.fingerprint) && typeof report.fingerprint.meaning === "string"
    ? report.fingerprint.meaning
    : "";
  const failureNote =
    Array.isArray(report.failures) && report.failures.length > 0
      ? ` · 退化 ${report.failures.length} 次`
      : "";
  return {
    sourceName,
    ok: true,
    summary: `${engine} · ${count} 条${failureNote}${fingerprint ? ` · ${fingerprint}` : ""}`,
  };
}

// ---------------------------------------------------------------------------
// 往返一致复核:写回后 doctor({yamls:[file]}) 对照 sources 名单
// ---------------------------------------------------------------------------

export interface RoundTripCheck {
  consistent: boolean;
  /** 写回应答承诺的启用名单 */
  expected: string[];
  /** doctor 实际报告的启用名单 */
  actual: string[];
  pluginFile: string;
  /** 不一致时的一行中文说明(供错误态展示) */
  message: string;
}

/** 名单一致 = 同一名字集合(忽略顺序;YAML 写回不承诺保序)。 */
export function sameNameSet(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sortedA = [...a].sort();
  const sortedB = [...b].sort();
  return sortedA.every((name, index) => name === sortedB[index]);
}

/**
 * doctor 复核往返:只对本次写回的品类 YAML 做体检,取该插件源名单与写回
 * 应答的 enabled 对照。doctor 的 findings 不在此解析(全面诊断属仪表盘),
 * 名单不一致即往返失败(结构化结果,不抛错 —— 供界面直接渲染)。
 */
export async function verifySourceRoundTrip(
  file: string,
  expected: string[],
): Promise<RoundTripCheck> {
  const doctor = await api.doctor({ yamls: [file] });
  const plugin = doctor.plugins.find((candidate) => candidate.file === file);
  const actual = plugin ? plugin.sources.map((source) => source.name) : [];
  const consistent = plugin !== undefined && sameNameSet(expected, actual);
  return {
    consistent,
    expected,
    actual,
    pluginFile: file,
    message: consistent
      ? "doctor 复核与写回结果一致"
      : plugin === undefined
        ? `doctor 未报告该品类(${file});写回可能未落盘或路径不一致`
        : `源名单不一致:期望 [${expected.join(", ") || "无"}],doctor 实际 [${actual.join(", ") || "无"}]`,
  };
}
