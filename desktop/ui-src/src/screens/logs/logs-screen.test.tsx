// @vitest-environment jsdom
/**
 * 采集日志组件测试(D4 结构性重做后)—— mock sidecar(vi.mock "@/lib/api":
 * run.status / logs.tail 返回夹具;onSidecarEvent 捕获处理器以注入
 * log/progress/completed 事件)。
 * 覆盖:run 瀑布分组 + 统计行(状态/耗时/条数/错误行数)/ 最新 run 自动展开与
 * 惰性 tail / 折叠-缓存-再展开不重拉 / 错误行 dead 高亮与级别着色(INFO 不再
 * 全染警示)/ 事件按 run_id 归组续播(含折叠组缓冲-展开合并)/ completed 刷新
 * 列表 / 结构化错误与空态。
 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    // INFO 级 stderr 行(本仓采集管线日志全走 stderr):正常运行日志,不染警示色
    { seq: 2, ts: "t2", run_id: 2, stream: "stderr" as const, line: "2026-10-02 12:00:00,001 INFO myia.pipeline: 运行开始 category=tech run_id=2 sources=1" },
    { seq: 3, ts: "t3", run_id: 2, stream: "stderr" as const, line: "ERROR source fetch failed: timeout" },
    { seq: 4, ts: "t4", run_id: 2, stream: "stderr" as const, line: "2026-10-02 12:00:01,002 WARNING 源限速 backoff 2s" },
    { seq: 5, ts: "t5", run_id: 2, stream: "stderr" as const, line: "2026-10-02 12:00:02,003 INFO myia.pipeline: 采集步骤完成 sources=1 items=5 source_failures=0" },
  ],
  total: 5,
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
  it("run 瀑布:统计行(状态/耗时/条数/错误行数)+ 最新 run 自动展开渲染 tail", async () => {
    mockSidecar([run2Running, run1Success]);
    render(<LogsScreen />);

    // 两个 run 组,新→旧;统计行带品类/耗时/条数/状态
    const header2 = await screen.findByTestId("run-group-header-2");
    expect(header2.textContent).toContain("daily-tech"); // record 缺失 → yaml 基名
    expect(header2.textContent).toContain("—"); // 运行中耗时未知
    expect(header2.getAttribute("aria-expanded")).toBe("true"); // 最新 run 自动展开
    const header1 = screen.getByTestId("run-group-header-1");
    expect(header1.textContent).toContain("科技资讯");
    expect(header1.textContent).toContain("1.2s");
    expect(header1.textContent).toContain("5 条");
    expect(header1.textContent).toContain("成功");
    expect(header1.getAttribute("aria-expanded")).toBe("false"); // 其余折叠

    // 自动展开 = 惰性 tail 拉取(run_id=2)
    await waitFor(() => expect(logsTailMock).toHaveBeenLastCalledWith({ lines: 400, run_id: 2 }));
    expect(screen.getByTestId("run-log-meta-2").textContent).toContain("run_id=2");

    // tail 五行:错误行 dead 高亮;WARNING 级警示;INFO 级不染;进度信号行不算错误
    const rows = await screen.findAllByTestId("log-row");
    expect(rows).toHaveLength(5);
    const errorRow = rows.find((row) => row.textContent?.includes("ERROR"));
    expect(errorRow?.getAttribute("data-error")).toBe("true");
    expect(errorRow?.className).toContain("text-dead"); // D4:错误行 dead 色
    expect(errorRow?.className).toContain("bg-dead");
    const warnRow = rows.find((row) => row.textContent?.includes("源限速"));
    expect(warnRow?.getAttribute("data-warn")).toBe("true");
    expect(warnRow?.getAttribute("data-error")).toBeNull();
    const infoRow = rows.find((row) => row.textContent?.includes("运行开始"));
    expect(infoRow?.getAttribute("data-error")).toBeNull();
    expect(infoRow?.getAttribute("data-warn")).toBeNull(); // INFO 级 stderr 不再全染警示
    const progressSignal = rows.find((row) => row.textContent?.includes("采集步骤完成"));
    expect(progressSignal?.getAttribute("data-error")).toBeNull();
    const normalRow = rows.find((row) => row.textContent?.includes("fetch https://"));
    expect(normalRow?.getAttribute("data-error")).toBeNull();
    expect(normalRow?.getAttribute("data-warn")).toBeNull();

    // 统计行与元信息条的错误行计数
    expect(screen.getByTestId("run-error-count-2").textContent).toBe("1 错误行");
    expect(screen.getByTestId("run-log-meta-2").textContent).toContain("5 行");
  });

  it("折叠交互:展开惰性拉取,折叠卸载,再展开走缓存不重拉", async () => {
    mockSidecar([run2Running, run1Success]);
    render(<LogsScreen />);
    await screen.findByTestId("run-group-header-2");
    await waitFor(() => expect(logsTailMock).toHaveBeenCalledTimes(1));

    // 展开 run 1 → 以 run_id=1 拉取,日志体挂载
    fireEvent.click(screen.getByTestId("run-group-header-1"));
    expect(screen.getByTestId("run-group-header-1").getAttribute("aria-expanded")).toBe("true");
    await waitFor(() => expect(logsTailMock).toHaveBeenLastCalledWith({ lines: 400, run_id: 1 }));
    expect(
      await within(screen.getByTestId("run-log-1")).findAllByTestId("log-row"),
    ).toHaveLength(5);

    // 折叠 → 日志体卸载(行不可见),不产生新请求
    fireEvent.click(screen.getByTestId("run-group-header-1"));
    expect(screen.getByTestId("run-group-header-1").getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("run-log-1")).toBeNull();

    // 再展开 → 缓存命中,不重拉(总调用数仍 2)
    fireEvent.click(screen.getByTestId("run-group-header-1"));
    expect(screen.getByTestId("run-group-header-1").getAttribute("aria-expanded")).toBe("true");
    expect(
      await within(screen.getByTestId("run-log-1")).findAllByTestId("log-row"),
    ).toHaveLength(5);
    expect(logsTailMock).toHaveBeenCalledTimes(2);
  });

  it("流式续播:事件按 run_id 归组;折叠组先缓冲、展开时合并到 tail 之后", async () => {
    mockSidecar([run2Running, run1Success]);
    render(<LogsScreen />);
    await screen.findByTestId("run-group-header-2");
    await screen.findAllByTestId("log-row");
    expect(harness.handler).not.toBeNull();

    // 他 run 的 log 事件:不入任何可见组(runs 列表也没有该组)
    emit({ type: "log", run_id: 99, stream: "stdout", line: "他 run 的行", ts: "t5" });
    expect(screen.queryByText("他 run 的行")).toBeNull();
    expect(screen.queryByTestId("run-group-99")).toBeNull();

    // 展开组(自动跟随的 run 2)的实时 log 行
    emit({ type: "log", run_id: 2, stream: "stdout", line: "实时输出行", ts: "t6" });
    expect(await screen.findByText(/实时输出行/)).toBeTruthy();

    // stderr 错误行实时高亮(dead)
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

    // 他 run 的 completed:只刷新列表,不落摘要行
    emit({ type: "completed", run_id: 99, exit_code: 2, status: "failed", dry: false, ts: "t10" });
    await waitFor(() => expect(runStatusMock).toHaveBeenCalledTimes(3));
    expect(screen.queryByText(/run 99 结束/)).toBeNull();

    // 折叠组(run 1)的迟到事件先缓冲;展开时 tail 打底 + 缓冲行合并其后
    emit({ type: "log", run_id: 1, stream: "stdout", line: "run1 迟到行", ts: "t11" });
    expect(screen.queryByText("run1 迟到行")).toBeNull(); // 折叠中不可见
    fireEvent.click(screen.getByTestId("run-group-header-1"));
    await screen.findAllByTestId("log-row");
    expect(screen.getByText("run1 迟到行")).toBeTruthy();
    const body = screen.getByTestId("run-log-1");
    const texts = Array.from(body.querySelectorAll('[data-testid="log-row"]')).map((el) => el.textContent);
    const bufferedIndex = texts.findIndex((t) => t?.includes("run1 迟到行"));
    expect(bufferedIndex).toBe(texts.length - 1); // 缓冲行排在 tail 历史之后
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

  it("空态:无 run 记录给引导文案,不渲染任何分组", async () => {
    mockSidecar([]);
    render(<LogsScreen />);

    expect(await screen.findByText(/还没有 run 记录/)).toBeTruthy();
    expect(screen.queryByTestId("run-group-header-1")).toBeNull();
    expect(screen.queryByTestId("run-group-header-2")).toBeNull();
  });
});
