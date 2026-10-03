// @vitest-environment jsdom
/**
 * 采集日志组件测试(D4 结构性重做 + G7 三件后)—— mock sidecar(vi.mock
 * "@/lib/api":run.status / logs.tail / run.start 返回夹具;onSidecarEvent
 * 捕获处理器以注入 log/progress/completed 事件)。
 * 覆盖:run 瀑布分组 + 统计行(状态/耗时/条数/错误行数)/ 最新 run 自动展开与
 * 惰性 tail / 折叠-缓存-再展开不重拉 / 错误行 dead 高亮(bg-dead/10,P2 对比
 * 实算 4.67:1)与级别着色(INFO 不再全染警示)/ 事件按 run_id 归组续播(含
 * 折叠组缓冲-展开合并)/ completed 刷新列表 / 结构化错误与空态;
 * G7:run 行重跑(骑 run.start 回放 yaml/dry/db + 触发反馈 + 列表刷新 + 错误
 * 反馈)/ 品类·状态过滤(过滤后空态 + 清除)/ 日志搜索(输入即过滤 + 命中
 * 高亮 + 命中计数 + 无命中文案)。
 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

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
      runStart: vi.fn(),
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
const runStartMock = vi.mocked(api.runStart);

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
    { seq: 2, ts: "t2", run_id: 2, stream: "stderr" as const, line: "2026-10-02 12:00:00,001 INFO shishi.pipeline: 运行开始 category=tech run_id=2 sources=1" },
    { seq: 3, ts: "t3", run_id: 2, stream: "stderr" as const, line: "ERROR source fetch failed: timeout" },
    { seq: 4, ts: "t4", run_id: 2, stream: "stderr" as const, line: "2026-10-02 12:00:01,002 WARNING 源限速 backoff 2s" },
    { seq: 5, ts: "t5", run_id: 2, stream: "stderr" as const, line: "2026-10-02 12:00:02,003 INFO shishi.pipeline: 采集步骤完成 sources=1 items=5 source_failures=0" },
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

// Radix Select 2.x 在 jsdom 里开下拉需要的指针捕获/滚动桩(品类·状态过滤用例;
// 同款见 dashboard-screen.test.tsx beforeAll)
beforeAll(() => {
  window.HTMLElement.prototype.hasPointerCapture = () => false;
  window.HTMLElement.prototype.releasePointerCapture = () => {};
  window.HTMLElement.prototype.scrollIntoView = () => {};
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
    // P2(G7 附带):对比公式实算 text-dead #e5484d 叠 bg-dead/10 于 bg-sidebar
    // #080b13 上 ≈ 4.67:1(原 /15 ≈ 4.43:1 不达 WCAG AA 正文 4.5)
    expect(errorRow?.className).toContain("bg-dead/10");
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

  // -------------------------------------------------------------------------
  // G7①:run 重跑(骑 G4 手动触发通道 run.start,不新增协议)
  // -------------------------------------------------------------------------

  it("重跑:行操作按原 run 的 yaml/dry/db 调 run.start,反馈新 run_id 并刷新列表", async () => {
    runStartMock.mockResolvedValue({
      run_id: 3,
      state: "running",
      yaml: "/plugins/tech.yaml",
      dry: false,
      db: "myia.db",
    });
    mockSidecar([run2Running, run1Success]);
    render(<LogsScreen />);
    await screen.findByTestId("run-group-header-1");
    const runStatusCalls = runStatusMock.mock.calls.length;

    fireEvent.click(screen.getByTestId("run-rerun-1"));
    // 回放原 run 三参数(dry/db 原样),不新增协议方法
    await waitFor(() =>
      expect(runStartMock).toHaveBeenCalledWith({ yaml: "/plugins/tech.yaml", dry: false, db: "myia.db" }),
    );
    // 触发成功反馈:新 run_id 上屏(新 run 的展开跟随由列表刷新的 follow 策略承担)
    expect(await screen.findByTestId("run-rerun-ok-1").then((el) => el.textContent)).toContain("已触发 #3");
    // 列表刷新一次(新 run 落瀑布)
    await waitFor(() => expect(runStatusMock.mock.calls.length).toBe(runStatusCalls + 1));
  });

  it("重跑失败:sidecar 结构化错误如实上屏(run_busy)", async () => {
    runStartMock.mockRejectedValue(
      new SidecarRequestError({ code: "run_busy", path: "$", message: "已有 run 在跑" }),
    );
    mockSidecar([run2Running, run1Success]);
    render(<LogsScreen />);
    await screen.findByTestId("run-group-header-1");

    fireEvent.click(screen.getByTestId("run-rerun-1"));
    const feedback = await screen.findByTestId("run-rerun-err-1");
    expect(feedback.textContent).toContain("run_busy");
    expect(feedback.textContent).toContain("已有 run 在跑");
  });

  // -------------------------------------------------------------------------
  // G7②:品类·状态过滤条(Radix Select:mouse 型 pointerDown 才开下拉)
  // -------------------------------------------------------------------------

  function openSelect(name: string) {
    fireEvent.pointerDown(screen.getByRole("combobox", { name }), { button: 0, pointerType: "mouse" });
  }

  it("品类过滤:选「科技资讯」只剩该品类 run,计数同步,清除后恢复", async () => {
    mockSidecar([run2Running, run1Success]);
    render(<LogsScreen />);
    await screen.findByTestId("run-group-header-2");
    expect(screen.getByTestId("logs-filter-count").textContent).toBe("2 / 2 run");

    openSelect("品类过滤");
    fireEvent.click(await screen.findByRole("option", { name: "科技资讯" }));

    await waitFor(() => expect(screen.queryByTestId("run-group-header-2")).toBeNull());
    expect(screen.getByTestId("run-group-header-1")).toBeTruthy();
    expect(screen.getByTestId("logs-filter-count").textContent).toBe("1 / 2 run");

    // 清除过滤(过滤激活时过滤条上的 ghost 按钮)→ 双 run 恢复
    fireEvent.click(screen.getByRole("button", { name: /清除过滤/ }));
    await waitFor(() => expect(screen.getByTestId("run-group-header-2")).toBeTruthy());
    expect(screen.getByTestId("logs-filter-count").textContent).toBe("2 / 2 run");
  });

  it("状态过滤 + 组合无命中:「运行中」只剩 running run;组合无命中给过滤空态", async () => {
    mockSidecar([run2Running, run1Success]);
    render(<LogsScreen />);
    await screen.findByTestId("run-group-header-2");

    openSelect("状态过滤");
    fireEvent.click(await screen.findByRole("option", { name: "运行中" }));

    await waitFor(() => expect(screen.queryByTestId("run-group-header-1")).toBeNull());
    expect(screen.getByTestId("run-group-header-2")).toBeTruthy(); // run 2 running

    // 组合:品类=科技资讯(终态成功)+ 状态=运行中 → 无命中,过滤空态
    openSelect("品类过滤");
    fireEvent.click(await screen.findByRole("option", { name: "科技资讯" }));
    expect(await screen.findByText(/没有匹配的 run/)).toBeTruthy();
    expect(screen.queryByTestId("run-group-header-1")).toBeNull();
    expect(screen.queryByTestId("run-group-header-2")).toBeNull();

    // 空态里的「清除过滤」一并恢复(过滤条上还有一个同名按钮,取空态内那个)
    const resetButtons = screen.getAllByRole("button", { name: /清除过滤/ });
    fireEvent.click(resetButtons[resetButtons.length - 1]);
    await waitFor(() => {
      expect(screen.getByTestId("run-group-header-1")).toBeTruthy();
      expect(screen.getByTestId("run-group-header-2")).toBeTruthy();
    });
  });

  // -------------------------------------------------------------------------
  // G7③:日志内搜索(输入即过滤 + 命中高亮)
  // -------------------------------------------------------------------------

  it("搜索:输入即过滤(大小写不敏感)+ 命中片段高亮 + 命中计数;清空恢复", async () => {
    mockSidecar([run2Running, run1Success]);
    render(<LogsScreen />);
    await screen.findAllByTestId("log-row");
    expect(screen.getAllByTestId("log-row")).toHaveLength(5);

    // 小写 "error" 命中大写 ERROR 行(stdout/WARNING/INFO 行全部滤除)
    fireEvent.change(screen.getByTestId("log-search-input"), { target: { value: "error" } });
    const rows = await screen.findAllByTestId("log-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("ERROR source fetch failed");
    expect(screen.getByTestId("run-log-hits-2").textContent).toContain("命中 1 / 5 行");

    // 命中片段 mark 高亮(只包住 ERROR 子串)
    const marks = screen.getAllByTestId("log-search-hit");
    expect(marks).toHaveLength(1);
    expect(marks[0].textContent).toBe("ERROR");

    // 无命中:该组给无命中文案,行集为空
    fireEvent.change(screen.getByTestId("log-search-input"), { target: { value: "zzz-不存在" } });
    expect(await screen.findByText(/无命中行/)).toBeTruthy();
    expect(screen.queryAllByTestId("log-row")).toHaveLength(0);
    expect(screen.getByTestId("run-log-hits-2").textContent).toContain("命中 0 / 5 行");

    // 清空 → 恢复五行
    fireEvent.change(screen.getByTestId("log-search-input"), { target: { value: "" } });
    await waitFor(() => expect(screen.getAllByTestId("log-row")).toHaveLength(5));
  });
});
