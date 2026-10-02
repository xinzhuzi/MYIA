// @vitest-environment jsdom
/**
 * 采集日志组件测试 —— mock sidecar(vi.mock "@/lib/api":run.status / logs.tail
 * 返回夹具;onSidecarEvent 捕获处理器以注入 log/progress/completed 事件)。
 * 覆盖:run 列表(状态/耗时/条目数) / tail 打底渲染与错误行高亮 / 事件流式续播
 * / completed 刷新列表 / 结构化错误与空态。
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SidecarRequestError } from "@/lib/api";
import type { RunEntry, SidecarEvent } from "@/lib/api";

const harness = vi.hoisted(() => ({
  handler: null as null | ((event: SidecarEvent) => void),
  unlisten: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      runStatus: vi.fn(),
      logsTail: vi.fn(),
    },
    onSidecarEvent: vi.fn((handler: (event: SidecarEvent) => void) => {
      harness.handler = handler;
      return Promise.resolve(harness.unlisten);
    }),
  };
});

const { api } = await import("@/lib/api");
const runStatusMock = vi.mocked(api.runStatus);
const logsTailMock = vi.mocked(api.logsTail);

import { LogsScreen } from "./logs-screen";

// ---------------------------------------------------------------------------
// 夹具(形状严格对齐 types.ts:RunEntry / LogsTailResult / 三类事件)
// ---------------------------------------------------------------------------

const run2Running: RunEntry = {
  run_id: 2,
  yaml: "/plugins/daily-tech.yaml",
  db: "myia.db",
  dry: false,
  state: "running",
  exit_code: null,
  status: null,
  started_at: "2026-10-02T12:00:00+00:00",
  finished_at: null,
  duration_ms: null,
  record: null,
};

const run1Success: RunEntry = {
  run_id: 1,
  yaml: "/plugins/tech.yaml",
  db: "myia.db",
  dry: false,
  state: "done",
  exit_code: 0,
  status: "success",
  started_at: "2026-10-02T08:00:00+00:00",
  finished_at: "2026-10-02T08:00:01+00:00",
  duration_ms: 1200,
  record: {
    run_id: 1,
    category: "科技资讯",
    status: "success",
    started_at: null,
    finished_at: null,
    stats: { items_retained: 5 },
    steps: null,
    error: null,
  },
};

const tailFixture = {
  lines: [
    { seq: 1, ts: "t1", run_id: 2, stream: "stdout" as const, line: "fetch https://example.com" },
    { seq: 2, ts: "t2", run_id: 2, stream: "stderr" as const, line: "ERROR source fetch failed: timeout" },
    { seq: 3, ts: "t3", run_id: 2, stream: "stderr" as const, line: "WARNING 源限速 backoff 2s" },
    { seq: 4, ts: "t4", run_id: 2, stream: "stderr" as const, line: "采集步骤完成 sources=1 items=5 source_failures=0" },
  ],
  total: 4,
  truncated: false,
};

function mockSidecar(runs: RunEntry[]) {
  runStatusMock.mockResolvedValue({ runs });
  logsTailMock.mockResolvedValue(tailFixture);
}

function emit(event: SidecarEvent) {
  act(() => {
    harness.handler?.(event);
  });
}

afterEach(() => {
  cleanup(); // vitest globals 关闭,RTL 自动清理不生效,须显式清理
  harness.handler = null;
  vi.resetAllMocks();
});

// ---------------------------------------------------------------------------
// 用例
// ---------------------------------------------------------------------------

describe("LogsScreen", () => {
  it("run 列表:状态/耗时/条目数;自动选中最新 run 并渲染 tail 历史", async () => {
    mockSidecar([run2Running, run1Success]);
    render(<LogsScreen />);

    // 列表:运行中 run 耗时 —、无条目数;完成 run 显示 1.2s 与 5 条
    await screen.findByTestId("run-row-2");
    expect(screen.getByTestId("run-row-2").textContent).toContain("daily-tech"); // record 缺失 → yaml 基名
    expect(screen.getByTestId("run-row-2").textContent).toContain("—");
    expect(screen.getByTestId("run-row-1").textContent).toContain("科技资讯");
    expect(screen.getByTestId("run-row-1").textContent).toContain("1.2s");
    expect(screen.getByTestId("run-row-1").textContent).toContain("5 条");

    // 自动选中最新(运行中的 run 2),终端标题与 tail 参数
    expect(await screen.findByTestId("terminal-title").then((el) => el.textContent)).toContain("run_id=2");
    await waitFor(() => expect(logsTailMock).toHaveBeenLastCalledWith({ lines: 400, run_id: 2 }));

    // tail 四行:错误行标红(data-error),stderr 非错误行弱警示(data-warn),
    // stderr 里的进度信号行(采集步骤完成)不算错误
    const rows = await screen.findAllByTestId("log-row");
    expect(rows).toHaveLength(4);
    const errorRow = rows.find((row) => row.textContent?.includes("ERROR"));
    expect(errorRow?.getAttribute("data-error")).toBe("true");
    const warnRow = rows.find((row) => row.textContent?.includes("源限速"));
    expect(warnRow?.getAttribute("data-warn")).toBe("true");
    expect(warnRow?.getAttribute("data-error")).toBeNull();
    const progressSignal = rows.find((row) => row.textContent?.includes("采集步骤完成"));
    expect(progressSignal?.getAttribute("data-error")).toBeNull();
    const normalRow = rows.find((row) => row.textContent?.includes("fetch https://"));
    expect(normalRow?.getAttribute("data-error")).toBeNull();
    expect(normalRow?.getAttribute("data-warn")).toBeNull();
    // 错误行计数徽标
    expect(screen.getByText("1 错误行")).toBeTruthy();
  });

  it("流式续播:log/progress 事件上屏,completed 落摘要并刷新 run 列表;他 run 事件不入屏", async () => {
    mockSidecar([run2Running, run1Success]);
    render(<LogsScreen />);
    await screen.findByTestId("run-row-2");
    await screen.findAllByTestId("log-row");
    expect(harness.handler).not.toBeNull();

    // 他 run 的 log 事件:不入当前终端
    emit({ type: "log", run_id: 99, stream: "stdout", line: "他 run 的行", ts: "t5" });
    expect(screen.queryByText("他 run 的行")).toBeNull();

    // 选中 run 的实时 log 行
    emit({ type: "log", run_id: 2, stream: "stdout", line: "实时输出行", ts: "t6" });
    expect(await screen.findByText(/实时输出行/)).toBeTruthy();

    // stderr 错误行实时高亮
    emit({ type: "log", run_id: 2, stream: "stderr", line: "ERROR 实时错误", ts: "t7" });
    await waitFor(() =>
      expect(screen.getByText(/实时错误/).getAttribute("data-error")).toBe("true"),
    );

    // progress 事件 → system 合成行(▸ 源完成 source=… items=…)
    emit({
      type: "progress",
      run_id: 2,
      phase: "source_done",
      source: "example",
      engine: "httpx",
      items: "3",
      ts: "t8",
    });
    const systemRow = await screen.findByTestId("log-system-row");
    expect(systemRow.textContent).toContain("源完成");
    expect(systemRow.textContent).toContain("source=example");

    // completed(选中 run)→ 摘要行 + 列表刷新(runStatus 第二次调用)
    emit({
      type: "completed",
      run_id: 2,
      exit_code: 0,
      status: "success",
      dry: false,
      duration_ms: 900,
      ts: "t9",
    });
    expect(await screen.findByText(/run 2 结束/).then((el) => el.textContent)).toContain("status=success");
    await waitFor(() => expect(runStatusMock).toHaveBeenCalledTimes(2));

    // 他 run 的 completed:只刷新列表,不落当前终端行
    emit({ type: "completed", run_id: 99, exit_code: 2, status: "failed", dry: false, ts: "t10" });
    await waitFor(() => expect(runStatusMock).toHaveBeenCalledTimes(3));
    expect(screen.queryByText(/run 99 结束/)).toBeNull();
  });

  it("点击列表切换选中 run:tail 以新 run_id 重拉", async () => {
    mockSidecar([run2Running, run1Success]);
    render(<LogsScreen />);
    await screen.findByTestId("run-row-2");
    await waitFor(() => expect(logsTailMock).toHaveBeenLastCalledWith({ lines: 400, run_id: 2 }));

    fireEvent.click(screen.getByTestId("run-row-1"));
    await waitFor(() => expect(logsTailMock).toHaveBeenLastCalledWith({ lines: 400, run_id: 1 }));
    expect(screen.getByTestId("terminal-title").textContent).toContain("run_id=1");
    expect(screen.getByTestId("terminal-title").textContent).toContain("科技资讯");
  });

  it("sidecar 结构化错误上屏(sidecar_not_running)", async () => {
    runStatusMock.mockRejectedValue(
      new SidecarRequestError({ code: "sidecar_not_running", path: "$", message: "sidecar 未运行" }),
    );
    logsTailMock.mockResolvedValue({ lines: [], total: 0, truncated: false });
    render(<LogsScreen />);

    const banner = await screen.findByTestId("logs-error");
    expect(banner.textContent).toContain("sidecar_not_running");
    expect(banner.textContent).toContain("sidecar 未运行");
  });

  it("空态:无 run 记录给引导文案,终端提示先选中", async () => {
    mockSidecar([]);
    render(<LogsScreen />);

    expect(await screen.findByText(/还没有 run 记录/)).toBeTruthy();
    expect(screen.getByTestId("terminal-title").textContent).toContain("未选中 run");
  });
});
