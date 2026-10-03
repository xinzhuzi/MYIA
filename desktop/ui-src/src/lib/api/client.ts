/**
 * MYIA 桌面 sidecar API client —— sidecar 协议(`desktop/entry.py`,23 方法)的共享封装:
 * 类型面 `SidecarProtocol` 盖 16 方法(核心 + image.*),`api` 门面只封装核心 10 方法
 * ——封装面 ≠ 协议面,分工见下方 api 对象头注释。
 *
 * 传输:壳命令 `sidecar_request`(src-tauri/src/main.rs);Rust 侧
 * Ok(Value) = 协议 result,Err(String) = 结构化错误对象 JSON 文本,
 * 这里统一解析为 SidecarRequestError 抛出(调用方 catch 后必得 code/path/message)。
 * 事件:壳把 sidecar stdout 的无 id 行原样 emit 为 `sidecar://event`。
 */
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import type {
  DoctorParams,
  DoctorResult,
  EmptyParams,
  HealthParams,
  HealthResult,
  LogsTailParams,
  LogsTailResult,
  PluginListResult,
  PluginsListParams,
  RunStartParams,
  RunStartResult,
  RunStatusParams,
  RunStatusResult,
  SidecarErrorShape,
  SidecarEvent,
  SidecarMethod,
  SidecarProtocol,
  SecretSetParams,
  SecretSetResult,
  SecretListResult,
  StoreItemsParams,
  StoreItemsResult,
  VersionParams,
  VersionResult,
} from "./types";

/** 壳侧流式事件名(与 main.rs `SIDECAR_EVENT` 常量一致) */
export const SIDECAR_EVENT_NAME = "sidecar://event";
/** 壳命令名(前端唯一入口,见 main.rs `#[tauri::command]`) */
const SIDECAR_COMMAND = "sidecar_request";

/** 结构化请求错误:任何 api.* 调用失败都抛本类型(code/path/message 必有)。 */
export class SidecarRequestError extends Error {
  readonly code: string;
  readonly path: string;
  readonly data: unknown;

  constructor(error: SidecarErrorShape) {
    super(error.message);
    this.name = "SidecarRequestError";
    this.code = error.code;
    this.path = error.path;
    this.data = error.data;
  }
}

/** 壳/环境层不可用(vite 浏览器直开、sidecar 未起等)时的兜底包装。 */
export class SidecarUnavailableError extends SidecarRequestError {
  constructor(detail: string) {
    super({ code: "sidecar_unavailable", path: "$", message: detail });
    this.name = "SidecarUnavailableError";
  }
}

/** Rust Err(String)/任意抛出 → 结构化错误对象。 */
function toSidecarError(raw: unknown): SidecarRequestError {
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
  if (raw instanceof Error) {
    return new SidecarUnavailableError(`Tauri IPC 不可用: ${raw.message}`);
  }
  return new SidecarUnavailableError(`Tauri IPC 不可用: ${String(raw)}`);
}

/** 类型化往返:params/result 由 SidecarProtocol 映射逐方法锁定。 */
async function request<M extends SidecarMethod>(
  method: M,
  params: SidecarProtocol[M]["params"],
): Promise<SidecarProtocol[M]["result"]> {
  try {
    return await invoke<SidecarProtocol[M]["result"]>(SIDECAR_COMMAND, {
      method,
      params: params ?? {},
    });
  } catch (raw) {
    throw toSidecarError(raw);
  }
}

/**
 * 共享类型化门面 —— 只封装核心 10 方法(version … secret.list),非协议全量。
 * 协议面(23 方法,单一事实源 = entry.py `_HANDLERS`,注册表见
 * .trellis/spec/desktop/sidecar-protocol.md)的其余方法走屏私有封装:
 * sources.write → screens/sources/api.ts、yaml.* → screens/yaml-editor/api.ts、
 * image.* → screens/image/api.ts(惯例:invoke 直连 + asSidecarError 归一化)。
 * 铁律:secret.set 的 value 只经本通道写入系统钥匙链,任何日志/界面零回显。
 */
export const api = {
  /** `myia --version` 等价:name/version/protocol */
  version: (params: VersionParams = {}): Promise<VersionResult> => request("version", params),
  /** 源健康度 + summary 聚合(健康度 ok/degraded/dead/unknown) */
  health: (params: HealthParams = {}): Promise<HealthResult> => request("health", params),
  /** 已装插件清单 + findings(v1.1 分级 tiers) */
  pluginsList: (params: PluginsListParams = {}): Promise<PluginListResult> =>
    request("plugins.list", params),
  /** 结构化诊断(问题全在 findings,完成即 0) */
  doctor: (params: DoctorParams = {}): Promise<DoctorResult> => request("doctor", params),
  /** 后台启动 run,立即返回 run_id;单飞(run_busy 拒绝并发) */
  runStart: (params: RunStartParams): Promise<RunStartResult> => request("run.start", params),
  /** run 注册表查询;run_id 缺省 = 全部(新→旧) */
  runStatus: (params: RunStatusParams = {}): Promise<RunStatusResult> =>
    request("run.status", params),
  /** 环形缓冲最近日志(可按 run_id 过滤) */
  logsTail: (params: LogsTailParams = {}): Promise<LogsTailResult> => request("logs.tail", params),
  /** 情报流条目(新→旧;SQLite 单库直读) */
  storeItems: (params: StoreItemsParams = {}): Promise<StoreItemsResult> =>
    request("store.items", params),
  /** 写凭据入系统钥匙链(值零回显) */
  secretSet: (params: SecretSetParams): Promise<SecretSetResult> => request("secret.set", params),
  /** 列凭据名(值永不可读) */
  secretList: (): Promise<SecretListResult> => request("secret.list", {} as EmptyParams),
} as const;

/** 订阅 sidecar 流式事件(log/progress/completed);返回取消订阅函数。 */
export function onSidecarEvent(handler: (event: SidecarEvent) => void): Promise<UnlistenFn> {
  return listen<SidecarEvent>(SIDECAR_EVENT_NAME, (event) => handler(event.payload));
}

export type { UnlistenFn };
export * from "./types";
