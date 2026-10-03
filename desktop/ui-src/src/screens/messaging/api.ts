/**
 * 消息屏数据装配(本屏私有 api 模块;共享客户端 @/lib/api 只读不动)。
 *
 * 数据面 = sidecar 协议消息方法族(task 10-03-messaging-ui;契约权威:任务档
 * design.md §D2 + entry.py `_m_channels_*` / `_m_push_write`,双侧同步):
 *
 *   channels.list    {} → {data_root, updated_at, platforms, aliases, dead, rules}
 *                      目录(platforms,条目名已套别名)+ 别名原始覆盖层(aliases)
 *                      + 死信键(dead,`platform:chat_id`)+ 推送规则视图(rules,
 *                      品类 YAML 的 push 条目;下区规则面板数据源)
 *   channels.refresh {platform} → {platform, merged, entries}
 *                      单平台目录发现→桶替换;unknown_platform /
 *                      discover_not_supported(telegram 被动积累)/
 *                      channel_refresh_failed(凭据/网络)结构化上抛
 *   channels.alias   {platform, chat_id, name} / {name: null} → set / delete
 *   push.write       {file, push} → {file, written, changed, backed_up?, push}
 *                      **push = 该文件完整 push 数组**(全量替换;空数组=摘除
 *                      push 节);服务端文本手术保注释,校验失败零写入
 *   bridge.status    {} → {available, reason, fix_hint, bin_found,
 *                      weixin_configured, gateway_alive, bin_path}
 *                      微信桥接探测(probe_bridge 全量;纯文件存在性检查,
 *                      零读取零出网;10-03-messaging-weixin-bridge D4,协议 v4 #31)
 *
 * 惯例与 sources 屏一致:invoke 直连壳命令 `sidecar_request` +
 * asSidecarError 归一化(错误必得 code/path/message)。
 */
import { invoke } from "@tauri-apps/api/core";

import { api, SidecarRequestError } from "@/lib/api";
import type { SidecarErrorShape } from "@/lib/api";

// ---------------------------------------------------------------------------
// 错误归一化(与 sources 屏同规则;独立实现避免动共享层)
// ---------------------------------------------------------------------------

/** 任意抛出物 → SidecarRequestError(组件渲染 code/path/message 用)。 */
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

// ---------------------------------------------------------------------------
// 协议类型(形状逐字段对照 desktop/entry.py 应答载荷)
// ---------------------------------------------------------------------------

/** 通道目录条目(directory.ChannelEntry.to_dict;name 已套别名覆盖)。 */
export interface ChannelEntry {
  platform: string;
  chat_id: string;
  name: string;
  type: string;
  thread_id: string | null;
  last_seen: number | null;
}

/** 别名原始覆盖层(手工可编文件 channel_aliases.json 的持久形态)。 */
export type AliasMap = Record<string, Record<string, string>>;

/**
 * 一条 push 条目的摘要 + 写回 base(rules 视图)。
 *
 * `platform=null` 表示该通道不支持目录寻址(webhook/stdout),UI 不给
 * targets 选择器;`raw` 是条目的最小无损形态 —— push.write 全量替换的
 * 写回 base(UI 改 targets 后 `{...raw, targets}` 整文件提交,None 字段
 * 与 webhook 专属传输字段已剔除,保证回传过服务端同门校验)。
 */
export interface PushRuleEntry {
  index: number;
  channel: string;
  platform: string | null;
  targets: string[];
  has_template: boolean;
  route_count: number;
  raw: Record<string, unknown>;
}

/** 一个品类 YAML 的 push 规则组(坏文件 parse_ok=false + error 如实入列)。 */
export interface PushRuleFile {
  file: string;
  category_id: string | null;
  category_name: string | null;
  parse_ok: boolean;
  error: { path: string; code: string; message: string } | null;
  entries: PushRuleEntry[];
}

/** channels.list 应答(目录+别名+死信+规则四视图)。 */
export interface ChannelsView {
  data_root: string;
  updated_at: string | null;
  platforms: Record<string, ChannelEntry[]>;
  aliases: AliasMap;
  dead: string[];
  rules: PushRuleFile[];
}

/** channels.refresh 应答。 */
export interface RefreshResult {
  platform: string;
  merged: number;
  entries: ChannelEntry[];
}

/** channels.alias 应答。 */
export interface AliasResult {
  platform: string;
  chat_id: string;
  deleted: boolean;
  name: string | null;
}

/** push.write 应答;push = 写回后该文件实际生效的 push 数组。 */
export interface PushWriteResult {
  file: string;
  written: true;
  changed: boolean;
  backed_up?: string | null;
  push: unknown[];
}

/** bridge.status 应答(微信桥接探测;形状逐字段对照 desktop/entry.py
 * `_m_bridge_status` → shishi.push.weixin.probe_bridge 的 BridgeStatus)。 */
export interface BridgeStatusView {
  available: boolean;
  reason: string | null;
  fix_hint: string | null;
  bin_found: boolean;
  weixin_configured: boolean;
  gateway_alive: boolean;
  bin_path: string;
}

// ---------------------------------------------------------------------------
// 协议方法封装(走壳命令 sidecar_request;方法名与 entry.py 双侧同步)
// ---------------------------------------------------------------------------

/** 目录四视图(零平台 = 合法空态,UI 给「先配平台凭据」指引)。 */
export async function channelsList(): Promise<ChannelsView> {
  try {
    return await invoke<ChannelsView>("sidecar_request", { method: "channels.list", params: {} });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 单平台目录发现(转圈态由调用方管理;失败结构化错误如实展示)。 */
export async function channelsRefresh(platform: string): Promise<RefreshResult> {
  try {
    return await invoke<RefreshResult>("sidecar_request", {
      method: "channels.refresh",
      params: { platform },
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 别名设置(写 channel_aliases.json;立即生效,目录重建后仍生效)。 */
export async function channelsAliasSet(
  platform: string,
  chat_id: string,
  name: string,
): Promise<AliasResult> {
  try {
    return await invoke<AliasResult>("sidecar_request", {
      method: "channels.alias",
      params: { platform, chat_id, name },
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 别名删除(条目名回退发现名;占位别名条目随之消失)。 */
export async function channelsAliasDelete(platform: string, chat_id: string): Promise<AliasResult> {
  try {
    return await invoke<AliasResult>("sidecar_request", {
      method: "channels.alias",
      params: { platform, chat_id, name: null },
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/**
 * push[] 全量替换写回(design.md D2 定案:完整写回而非增量 patch)。
 *
 * push 必须是该文件**完整** push 数组 —— UI 侧「编辑一条提交整个数组」;
 * 同平台约束等校验在服务端 load_category 同门把关,失败零写入
 * (结构化错误原样带回,界面不假装成功)。
 */
export async function pushWrite(file: string, push: unknown[]): Promise<PushWriteResult> {
  try {
    return await invoke<PushWriteResult>("sidecar_request", {
      method: "push.write",
      params: { file, push },
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/**
 * 微信桥接探测(10-03-messaging-weixin-bridge D4;屏私有封装,invoke 直连
 * 不入共享门面——sidecar-protocol.md 变更纪律 3)。
 *
 * 纯展示信号:调用方对失败降级为 null(平台卡按灰态「需本机 Hermes」
 * 呈现,不挡整屏目录视图;发送期的结构化 `bridge_unavailable` 是通道层事)。
 */
export async function bridgeStatus(): Promise<BridgeStatusView> {
  try {
    return await invoke<BridgeStatusView>("sidecar_request", { method: "bridge.status", params: {} });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

// ---------------------------------------------------------------------------
// 视图装配纯函数(展示格式化,零协议往返)
// ---------------------------------------------------------------------------

/** last_seen(Unix 秒)→ 本地日期时间串;null → "—"(从未发现)。 */
export function formatLastSeen(lastSeen: number | null): string {
  if (lastSeen === null || lastSeen === undefined) return "—";
  const date = new Date(lastSeen * 1000);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** 目录条目 → targets spec(`platform:名称`;与 schema 契约同源)。 */
export function targetSpec(entry: ChannelEntry): string {
  return `${entry.platform}:${entry.name}`;
}

/** 该条目是否已标死信(dead 键 = `platform:chat_id`,小写平台前缀)。 */
export function isDeadEntry(entry: ChannelEntry, dead: string[]): boolean {
  return dead.includes(`${entry.platform}:${entry.chat_id}`);
}

/** 该条目名是否来自手工别名(区分「发现名/手工命名」)。 */
export function hasAlias(entry: ChannelEntry, aliases: AliasMap): boolean {
  return Boolean(aliases[entry.platform]?.[entry.chat_id]);
}

// ---------------------------------------------------------------------------
// 平台总览数据(task 10-03-messaging-platforms R3:凭据探测,零新协议方法)
// ---------------------------------------------------------------------------

/**
 * 钥匙链凭据名清单(既有 secret.list;平台卡「已连接/需要设置」派生的
 * 凭据信号)。只有名字,值永不可读;调用方对失败降级为空名单
 * (钥匙链不可用的主机上,平台状态回退到「目录非空」单一信号)。
 */
export async function listSecretNames(): Promise<string[]> {
  return (await api.secretList()).names;
}
