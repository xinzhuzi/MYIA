// @vitest-environment jsdom
//
// use-sidecar-status 状态机测试(v1.1.2 桌面对齐批 C2):
// 探测在线 / 探测失败→壳层 sidecar_restart 拉起→再探测(offline 与救回两形)/
// 壳事件 sidecar://state 的 respawning/dead/online 三态入态机。
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  invoke: vi.fn(),
  listen: vi.fn(),
  version: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, version: mocks.version },
  };
});

import { useSidecarStatus } from "@/hooks/use-sidecar-status";
import { SidecarRequestError } from "@/lib/api";

type StateHandler = (event: { payload: { state: string; attempt?: number; respawned?: boolean } }) => void;

function installListen() {
  let handler: StateHandler | null = null;
  mocks.listen.mockImplementation(async (_name: string, fn: StateHandler) => {
    handler = fn;
    return () => undefined;
  });
  return {
    emit: (payload: { state: string; attempt?: number; respawned?: boolean }) => {
      act(() => handler?.({ payload }));
    },
  };
}

function transportError(code = "sidecar_not_running"): SidecarRequestError {
  return new SidecarRequestError({ code, path: "$", message: "sidecar 进程未运行" });
}

beforeEach(() => {
  mocks.invoke.mockReset();
  mocks.listen.mockReset();
  mocks.version.mockReset();
  mocks.invoke.mockResolvedValue({ restarted: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useSidecarStatus(C2:探测+拉起+壳事件)", () => {
  it("探测成功 → online(不调拉起)", async () => {
    installListen();
    mocks.version.mockResolvedValue({ name: "shishi", version: "1.1.1", protocol: 2 });

    const { result } = renderHook(() => useSidecarStatus());
    await waitFor(() => expect(result.current.status).toBe("online"));
    expect(mocks.invoke).not.toHaveBeenCalled();
    expect(result.current.info?.protocol).toBe(2);
  });

  it("探测失败 → 壳命令 sidecar_restart 拉起 → 再探测仍败 → offline(错误保留)", async () => {
    installListen();
    mocks.version.mockRejectedValue(transportError());

    const { result } = renderHook(() => useSidecarStatus());
    await waitFor(() => expect(result.current.status).toBe("offline"));
    expect(mocks.invoke).toHaveBeenCalledWith("sidecar_restart");
    expect(mocks.version).toHaveBeenCalledTimes(2); // 探测→拉起→再探测
    expect(result.current.error?.code).toBe("sidecar_not_running");
  });

  it("探测失败 → 拉起后探测成功 → online(救回,C2 核心场景)", async () => {
    installListen();
    mocks.version
      .mockRejectedValueOnce(transportError("sidecar_terminated"))
      .mockResolvedValueOnce({ name: "shishi", version: "1.1.1", protocol: 2 });

    const { result } = renderHook(() => useSidecarStatus());
    await waitFor(() => expect(result.current.status).toBe("online"));
    expect(mocks.invoke).toHaveBeenCalledWith("sidecar_restart");
    expect(result.current.error).toBeNull();
  });

  it("壳事件:respawning → 重拉中;dead → dead 态(可手动拉起);online → 触发重探测", async () => {
    const events = installListen();
    mocks.version.mockResolvedValue({ name: "shishi", version: "1.1.1", protocol: 2 });
    const { result } = renderHook(() => useSidecarStatus());
    await waitFor(() => expect(result.current.status).toBe("online"));

    events.emit({ state: "respawning", attempt: 2 });
    expect(result.current.status).toBe("respawning");

    events.emit({ state: "dead", attempt: 5 });
    expect(result.current.status).toBe("dead");
    expect(result.current.error?.code).toBe("sidecar_respawn_exhausted");

    // online 事件 → 重探测回在线态(探测是唯一真相源)
    events.emit({ state: "online", respawned: true });
    await waitFor(() => expect(result.current.status).toBe("online"));
    expect(mocks.version).toHaveBeenCalledTimes(2);
  });

  it("浏览器直开:listen 不可用不崩,invoke 失败按 offline 呈现", async () => {
    mocks.listen.mockRejectedValue(new Error("no tauri"));
    mocks.invoke.mockRejectedValue(new Error("no tauri"));
    mocks.version.mockRejectedValue(transportError("sidecar_unavailable"));

    const { result } = renderHook(() => useSidecarStatus());
    await waitFor(() => expect(result.current.status).toBe("offline"));
    expect(result.current.error?.code).toBe("sidecar_unavailable");
  });
});
