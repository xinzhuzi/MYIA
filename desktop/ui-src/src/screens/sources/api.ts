/**
 * 源管理数据装配(本屏私有 api 模块;共享客户端 @/lib/api 只读不动)。
 *
 * 数据面 = sidecar 协议既有方法(entry.py `_HANDLERS`):
 *   health → myia list --json 等价:插件清单 + 源健康度(ok/degraded/dead/unknown)
 *            + summary 聚合;doctor → myia doctor --json 等价(结构化 findings)。
 *
 * 启停写回(协议扩展提案 `sources.write`)::
 *   entry.py 现有方法集没有品类 YAML 写回能力(A 阶段协议只覆盖只读 + run +
 *   secret)。本模块按下列契约调用扩展方法,并独立走 invoke 通道(与共享
 *   client 同一壳命令 `sidecar_request`,错误归一化逻辑一致);entry.py 收编
 *   该方法前,运行时将得到结构化 method_not_found(allowed 列表内无此方法),
 *   界面按错误态展示 —— 这是「协议缺口」的如实呈现,不是 UI 缺陷。
 *
 *   请求  {"method": "sources.write", "params": {"file", "enable"?, "disable"?}}
 *   应答  {"file", "written": true, "enabled": string[], "disabled": string[]}
 *   语义  file = health 报告里的品类 YAML 路径原样回传;disable 把源从
 *         sources: 摘出(lossless 暂存,建议实现:顶层 disabled_sources: 节,
 *         存储细节属 Python 侧自由度,本契约只钉住名字集合往返);enable 移回。
 *   往返一致 = 写回后 doctor({yamls:[file]}) 报告的 sources 名单与应答
 *         enabled 完全一致(见 verifySourceRoundTrip)。
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
  };
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
 * 启停写回:经壳命令 sidecar_request 调 `sources.write`(协议扩展提案)。
 * entry.py 收编前运行时必得 method_not_found —— 调用方按结构化错误渲染。
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
