import { useCallback, useEffect, useRef, useState } from "react";

import { api, SidecarRequestError } from "@/lib/api";
import type { VersionResult } from "@/lib/api";

/** sidecar 全局连接状态:connecting 探测中 / online 已连 / offline 不可达 */
export type SidecarStatus = "connecting" | "online" | "offline";

interface SidecarStatusState {
  status: SidecarStatus;
  /** 探测成功时的 version 方法结果(name/version/protocol) */
  info: VersionResult | null;
  /** 探测失败的结构化原因(code/path/message) */
  error: SidecarRequestError | null;
}

/** Hook 返回:状态 + 重新探测(离线态的重试按钮) */
export type SidecarStatusStateWithReprobe = SidecarStatusState & { reprobe: () => void };

/**
 * 全局状态(顶栏):探测一次 `version` 判定 sidecar 可达性。
 * 浏览器直开(无 Tauri IPC)或 sidecar 未起 → offline,界面照常可用。
 * C 阶段可扩展:订阅 `sidecar://event` 把 run 活动并入全局状态。
 */
export function useSidecarStatus(): SidecarStatusStateWithReprobe {
  const [state, setState] = useState<SidecarStatusState>({
    status: "connecting",
    info: null,
    error: null,
  });
  const disposed = useRef(false);

  const probe = useCallback(async () => {
    setState((prev) => ({ ...prev, status: "connecting" }));
    try {
      const info = await api.version();
      if (!disposed.current) setState({ status: "online", info, error: null });
    } catch (error) {
      if (!disposed.current) {
        setState({
          status: "offline",
          info: null,
          error:
            error instanceof SidecarRequestError
              ? error
              : new SidecarRequestError({
                  code: "transport_error",
                  path: "$",
                  message: String(error),
                }),
        });
      }
    }
  }, []);

  useEffect(() => {
    disposed.current = false;
    void probe();
    return () => {
      disposed.current = true;
    };
  }, [probe]);

  return { ...state, reprobe: probe };
}
