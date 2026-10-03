// @vitest-environment jsdom
/**
 * 顶栏全局「跑一次」测试(C12,10-03-v112-desktop-parity):品类定位
 * (health id 匹配 / 全部 = 第一个可加载)→ run.start;collecting 态 ✕ →
 * run.cancel;completed 事件收尾;run_busy 错误透传(文案指向取消)。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  health: vi.fn(),
  runStart: vi.fn(),
  runCancel: vi.fn(),
  onSidecarEvent: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, health: mocks.health, runStart: mocks.runStart, runCancel: mocks.runCancel },
    onSidecarEvent: mocks.onSidecarEvent,
  };
});

import { GlobalRun } from "@/components/layout/global-run";
import { SidecarRequestError } from "@/lib/api";
import type { HealthResult } from "@/lib/api";

function healthOf(plugins: { file: string; id: string | null; loaded: boolean }[]): HealthResult {
  return {
    command: "list",
    plugins_dir: "/plugins",
    db: "myia.db",
    store_error: null,
    plugins: plugins.map((entry) => ({
      file: entry.file,
      id: entry.id,
      name: entry.id ?? entry.file,
      schedule: null,
      timezone: null,
      push_channels: [],
      loaded: entry.loaded,
      load_errors: null,
      sources: [],
    })),
    summary: { plugins: plugins.length, sources: 0, ok: 0, degraded: 0, dead: 0, unknown: 0 },
    healthy: true,
    first_run: false,
    exit_code: 0,
  } as HealthResult;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// onSidecarEvent 订阅缺省:resolved unlisten(真实实现恒返回 Promise;
// 专项用例自覆写;top-bar.test 同款约定)
beforeEach(() => {
  mocks.onSidecarEvent.mockResolvedValue(() => undefined);
});

describe("GlobalRun(C12 顶栏跑一次)", () => {
  it("选中品类 → health 按 id 定位 YAML → run.start", async () => {
    mocks.health.mockResolvedValue(
      healthOf([
        { file: "/plugins/tech.yaml", id: "tech", loaded: true },
        { file: "/plugins/stocks.yaml", id: "stocks", loaded: true },
      ]),
    );
    mocks.runStart.mockResolvedValue({
      run_id: 3,
      state: "running",
      yaml: "/plugins/stocks.yaml",
      dry: false,
      db: "myia.db",
    });
    render(<GlobalRun category="stocks" />);

    fireEvent.click(screen.getByLabelText("跑一次:stocks"));
    await waitFor(() => expect(mocks.runStart).toHaveBeenCalledWith({ yaml: "/plugins/stocks.yaml" }));
    await waitFor(() => expect(screen.getByTestId("global-run-collecting")).toBeTruthy());
  });

  it("全部品类(null)→ 第一个可加载插件;装不上的(loaded=false)跳过", async () => {
    mocks.health.mockResolvedValue(
      healthOf([
        { file: "/plugins/broken.yaml", id: null, loaded: false },
        { file: "/plugins/tech.yaml", id: "tech", loaded: true },
      ]),
    );
    mocks.runStart.mockResolvedValue({
      run_id: 4,
      state: "running",
      yaml: "/plugins/tech.yaml",
      dry: false,
      db: "myia.db",
    });
    render(<GlobalRun category={null} />);

    fireEvent.click(screen.getByLabelText("跑一次(第一个可用品类)"));
    await waitFor(() => expect(mocks.runStart).toHaveBeenCalledWith({ yaml: "/plugins/tech.yaml" }));
  });

  it("collecting 态 ✕ → run.cancel(run_id 定向);受理后回 idle", async () => {
    mocks.health.mockResolvedValue(healthOf([{ file: "/plugins/tech.yaml", id: "tech", loaded: true }]));
    mocks.runStart.mockResolvedValue({
      run_id: 9,
      state: "running",
      yaml: "/plugins/tech.yaml",
      dry: false,
      db: "myia.db",
    });
    mocks.runCancel.mockResolvedValue({ run_id: 9, cancelled: true, state: "running" });
    render(<GlobalRun category="tech" />);

    fireEvent.click(screen.getByLabelText("跑一次:tech"));
    await waitFor(() => expect(screen.getByTestId("global-run-collecting")).toBeTruthy());
    fireEvent.click(screen.getByLabelText("取消采集"));
    await waitFor(() => expect(mocks.runCancel).toHaveBeenCalledWith({ run_id: 9 }));
    await waitFor(() => expect(screen.getByLabelText("跑一次:tech")).toBeTruthy());
  });

  it("completed 事件(run_id 匹配)→ 自动回 idle;订阅随 collecting 起止", async () => {
    type Emitter = (event: { type: string; run_id: number }) => void;
    // holder 对象属性:绕开 TS 对闭包内赋值变量的 never 收窄
    const holder: { emit: Emitter | null } = { emit: null };
    mocks.onSidecarEvent.mockImplementation(async (handler: Emitter) => {
      holder.emit = handler;
      return () => undefined;
    });
    mocks.health.mockResolvedValue(healthOf([{ file: "/plugins/tech.yaml", id: "tech", loaded: true }]));
    mocks.runStart.mockResolvedValue({
      run_id: 11,
      state: "running",
      yaml: "/plugins/tech.yaml",
      dry: false,
      db: "myia.db",
    });
    render(<GlobalRun category="tech" />);

    fireEvent.click(screen.getByLabelText("跑一次:tech"));
    await waitFor(() => expect(screen.getByTestId("global-run-collecting")).toBeTruthy());
    holder.emit?.({ type: "completed", run_id: 11 });
    await waitFor(() => expect(screen.getByLabelText("跑一次:tech")).toBeTruthy());
  });

  it("run_busy 结构化错误透传(忙碌语义指向取消按钮)", async () => {
    mocks.health.mockResolvedValue(healthOf([{ file: "/plugins/tech.yaml", id: "tech", loaded: true }]));
    mocks.runStart.mockRejectedValue(
      new SidecarRequestError({
        code: "run_busy",
        path: "$",
        message: "已有 run 在执行 run_id=2(桌面单飞;请等待 completed 事件)",
        data: { active_run_id: 2 },
      }),
    );
    render(<GlobalRun category="tech" />);

    fireEvent.click(screen.getByLabelText("跑一次:tech"));
    await waitFor(() => expect(screen.getByTestId("global-run-error").textContent).toContain("run_busy"));
    // 失败可重试:idle 态按钮仍在(本项目无 jest-dom,原生 disabled 直查)
    expect((screen.getByLabelText("跑一次:tech") as HTMLButtonElement).disabled).toBe(false);
  });

  it("插件目录为空:如实报错不抛", async () => {
    mocks.health.mockResolvedValue(healthOf([]));
    render(<GlobalRun category={null} />);

    fireEvent.click(screen.getByLabelText("跑一次(第一个可用品类)"));
    await waitFor(() =>
      expect(screen.getByTestId("global-run-error").textContent).toContain("插件目录为空"),
    );
    expect(mocks.runStart).not.toHaveBeenCalled();
  });
});
