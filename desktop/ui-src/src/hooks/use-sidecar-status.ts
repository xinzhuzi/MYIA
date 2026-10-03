import { useCallback, useEffect, useRef, useState } from "react";

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import { api, SidecarRequestError } from "@/lib/api";
import type { VersionResult } from "@/lib/api";

/**
 * sidecar 全局连接状态:
 * connecting 探测中 / online 已连 / offline 不可达 /
 * respawning 壳层自动重拉中(退避序列内)/ dead 自动重拉超限(待手动拉起)。
 * 后两态来自壳层 `sidecar://state` 事件(main.rs respawn,C2)。
 */
export type SidecarStatus = "connecting" | "online" | "offline" | "respawning" | "dead";

/** 壳层 `sidecar://state` 事件载荷(与 main.rs `SIDECAR_STATE_EVENT` 同形) */
export interface SidecarStateEvent {
  state: "respawning" | "online" | "dead";
  attempt?: number;
  respawned?: boolean;
}

interface SidecarStatusState {
  status: SidecarStatus;
  /** 探测成功时的 version 方法结果(name/version/protocol) */
  info: VersionResult | null;
  /** 探测失败的结构化原因(code/path/message) */
  error: SidecarRequestError | null;
}

/** Hook 返回:状态 + 重新探测(离线/死亡态的动作按钮) */
export type SidecarStatusStateWithReprobe = SidecarStatusState & { reprobe: () => void };

/** 探测一次 version;失败产出结构化 error(浏览器直开 = sidecar_unavailable)。 */
async function probeVersion(): Promise<
  { online: true; info: VersionResult } | { online: false; error: SidecarRequestError }
> {
  try {
    return { online: true, info: await api.version() };
  } catch (error) {
    return {
      online: false,
      error:
        error instanceof SidecarRequestError
          ? error
          : new SidecarRequestError({
              code: "transport_error",
              path: "$",
              message: String(error),
            }),
    };
  }
}

/** 调壳命令 `sidecar_restart` 手动拉起(幂等:进程健在时壳侧原样返回)。 */
async function invokeSidecarRestart(): Promise<boolean> {
  try {
    const result = (await invoke("sidecar_restart")) as { restarted?: boolean };
    return result?.restarted !== false;
  } catch {
    return false; // 浏览器直开/壳不可达:拉起失败,探测兜底呈现 offline
  }
}

/**
 * 全局状态(顶栏):探测 `version` 判定 sidecar 可达性;
 * 订阅壳层 `sidecar://state` 把 respawn 生命周期(respawning/dead/online)如实入态;
 * reprobe 升级为「探测 → 失败时调 sidecar_restart 拉起 → 再探测」(C2:
 * 纯重探测救不回已死的 sidecar,拉起才是修复动作)。
 */
export function useSidecarStatus(): SidecarStatusStateWithReprobe {
  const [state, setState] = useState<SidecarStatusState>({
    status: "connecting",
    info: null,
    error: null,
  });
  const disposed = useRef(false);
  const probing = useRef(false);

  const probe = useCallback(async () => {
    if (probing.current) return; // 重入防御(事件触发的探测与按钮点击竞态)
    probing.current = true;
    setState((prev) => ({ ...prev, status: "connecting" }));
    const first = await probeVersion();
    if (first.online) {
      if (!disposed.current) setState({ status: "online", info: first.info, error: null });
      probing.current = false;
      return;
    }
    // 探测失败:先尝试壳层拉起再探一次(offline 的修复路径,C2)
    await invokeSidecarRestart();
    const second = await probeVersion();
    if (!disposed.current) {
      setState(
        second.online
          ? { status: "online", info: second.info, error: null }
          : { status: "offline", info: null, error: second.error ?? first.error },
      );
    }
    probing.current = false;
  }, []);

  useEffect(() => {
    disposed.current = false;
    void probe();
    let unlisten: UnlistenFn | null = null;
    let cancelled = false;
    // 壳层生命周期事件:respawning/dead 如实入态;online 触发重探测(探测是唯一真相源)
    void listen<SidecarStateEvent>("sidecar://state", (event) => {
      if (cancelled) return;
      const payload = event.payload;
      if (payload.state === "respawning" || payload.state === "dead") {
        setState((prev) => ({
          ...prev,
          status: payload.state,
          error:
            payload.state === "dead"
              ? new SidecarRequestError({
                  code: "sidecar_respawn_exhausted",
                  path: "$",
                  message: `sidecar 自动重拉超限(${payload.attempt ?? "?"} 次),请手动拉起`,
                })
              : prev.error,
        }));
      } else {
        void probe();
      }
    })
      .then((unlistenFn) => {
        if (cancelled) unlistenFn();
        else unlisten = unlistenFn;
      })
      .catch(() => {
        // 浏览器直开无事件通道:探测态已够用,零行为变化
      });
    return () => {
      disposed.current = true;
      cancelled = true;
      unlisten?.();
    };
  }, [probe]);

  return { ...state, reprobe: probe };
}
