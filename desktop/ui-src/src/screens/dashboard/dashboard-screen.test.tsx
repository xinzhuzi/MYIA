// @vitest-environment jsdom
/**
 * 仪表盘组件测试 —— mock sidecar(vi.mock "@/lib/api" 的 api 门面,
 * doctor / runs.list / run.status 返回夹具;错误用真实 SidecarRequestError 注入)。
 * 覆盖:品类状态卡(含载入失败) / 源健康度四态汇总 / 近期 run 成功率
 * (runs.list 历史行 + run.status 活跃叠加,C3)/ 错误与空态。
 */
import { cleanup, fireEvent, render, screen, waitFor, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SidecarRequestError } from "@/lib/api";
import type { DoctorResult, RunEntry, RunRecord, SourceHealthState } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      doctor: vi.fn(),
      runsList: vi.fn(),
      runStatus: vi.fn(),
      runStart: vi.fn(),
    },
    onSidecarEvent: vi.fn(),
  };
});

const { api, onSidecarEvent } = await import("@/lib/api");
const doctorMock = vi.mocked(api.doctor);
const runsListMock = vi.mocked(api.runsList);
const runStatusMock = vi.mocked(api.runStatus);
const runStartMock = vi.mocked(api.runStart);
const onSidecarEventMock = vi.mocked(onSidecarEvent);

import { DashboardScreen } from "./dashboard-screen";

// ---------------------------------------------------------------------------
// 夹具(形状严格对齐 types.ts:DoctorResult / RunEntry)
// ---------------------------------------------------------------------------

function fixtureSource(name: string, state: SourceHealthState) {
  return {
    name,
    url: `https://example.com/${name}`,
    engine: "static_html",
    engine_hint: null,
    health: { state, reason: state === "dead" ? "连续无产出" : "", observed: 5, latest: null, baseline: 2 },
    fingerprint_skips: { observed: 0, skipped: 0 },
  };
}

function fixturePlugin(overrides: Partial<DoctorResult["plugins"][number]>) {
  return {
    file: "tech.yaml",
    id: "tech",
    name: "科技资讯",
    schedule: "0 9 * * *",
    timezone: "Asia/Shanghai",
    push_channels: [],
    loaded: true,
    load_errors: null,
    sources: [],
    next_fire_at: "2026-10-03T09:00:00+08:00",
    enrich: null,
    ...overrides,
  };
}

function fixtureDoctor(overrides: Partial<DoctorResult> = {}): DoctorResult {
  return {
    command: "doctor",
    generated_at: "2026-10-02T12:00:00+00:00",
    db: "myia.db",
    healthy: true,
    plugins: [
      fixturePlugin({
        sources: [fixtureSource("hn", "ok"), fixtureSource("gh", "ok"), fixtureSource("blog", "degraded")],
      }),
    ],
    credentials: { backend_available: true, backend_error: null, entries: [] },
    proxy: { config: null, pools: [] },
    findings: [],
    summary: { plugins: 1, sources: 3, errors: 0, warnings: 0 },
    ...overrides,
  };
}

let nextRunId = 0;
/** runs.list 历史行(runs 表直读形状,types.ts RunRecord) */
function fixtureHistoryRun(overrides: Partial<RunRecord> = {}): RunRecord {
  nextRunId += 1;
  return {
    run_id: nextRunId,
    category: "tech",
    status: "success",
    started_at: "2026-10-02T08:00:00+00:00",
    finished_at: "2026-10-02T08:00:01+00:00",
    stats: { items_retained: 5 },
    steps: null,
    error: null,
    ...overrides,
  };
}

/** run.status 内存注册表条目(仅活跃叠加用;entry.py `_m_run_status`) */
function fixtureRegistryRun(overrides: Partial<RunEntry> = {}): RunEntry {
  return {
    run_id: 9000,
    yaml: "/plugins/tech.yaml",
    db: "myia.db",
    dry: false,
    state: "running",
    exit_code: null,
    status: null,
    started_at: "2026-10-03T08:00:00+00:00",
    finished_at: null,
    duration_ms: null,
    record: null,
    ...overrides,
  };
}

function mockSidecar(
  doctor: Promise<DoctorResult>,
  history: Promise<{ runs: RunRecord[] }>,
  registry: Promise<{ runs: RunEntry[] }> = Promise.resolve({ runs: [] }),
) {
  doctorMock.mockReturnValue(doctor);
  // runs.list 应答形 = RunsListResult(db/count/runs);夹具只给 runs,补外层
  runsListMock.mockReturnValue(history.then((payload) => ({ db: "myia.db", count: payload.runs.length, ...payload })));
  runStatusMock.mockReturnValue(registry);
}

afterEach(() => {
  cleanup(); // vitest globals 关闭,RTL 自动清理不生效,须显式清理
  vi.resetAllMocks();
  nextRunId = 0;
});

// ---------------------------------------------------------------------------
// 用例
// ---------------------------------------------------------------------------

describe("DashboardScreen", () => {
  it("渲染品类状态卡:健康品类=正常,载入失败品类=异常", async () => {
    mockSidecar(
      Promise.resolve(
        fixtureDoctor({
          plugins: [
            fixturePlugin({ file: "tech.yaml", sources: [fixtureSource("hn", "ok")] }),
            fixturePlugin({
              file: "broken.yaml",
              name: "坏品类",
              loaded: false,
              load_errors: [{ error_type: "config_error", path: "$", message: "schema 拒载" }],
            }),
          ],
          findings: [
            {
              severity: "error",
              scope: "plugin:broken.yaml",
              code: "config_error",
              message: "schema 拒载",
            },
          ],
          healthy: false,
          summary: { plugins: 2, sources: 1, errors: 1, warnings: 0 },
        }),
      ),
      Promise.resolve({ runs: [] }),
    );
    render(<DashboardScreen />);

    await screen.findByText("科技资讯");
    expect(screen.getByText("坏品类")).toBeTruthy();
    const healthy = screen.getByTestId("category-tech.yaml");
    expect(healthy.textContent).toContain("正常");
    const broken = screen.getByTestId("category-broken.yaml");
    expect(broken.textContent).toContain("异常");
    expect(broken.textContent).toContain("未载入");
    expect(screen.getByTestId("dashboard-screen-root")).toBeTruthy();
  });

  it("源健康度汇总按四态计数(ok/degraded/dead/unknown)", async () => {
    mockSidecar(
      Promise.resolve(
        fixtureDoctor({
          plugins: [
            fixturePlugin({
              sources: [
                fixtureSource("a", "ok"),
                fixtureSource("b", "ok"),
                fixtureSource("c", "degraded"),
                fixtureSource("d", "dead"),
                fixtureSource("e", "unknown"),
              ],
            }),
          ],
        }),
      ),
      Promise.resolve({ runs: [] }),
    );
    render(<DashboardScreen />);

    await screen.findByText("科技资讯");
    expect(screen.getByTestId("health-ok").textContent).toContain("2");
    expect(screen.getByTestId("health-degraded").textContent).toContain("1");
    expect(screen.getByTestId("health-dead").textContent).toContain("1");
    expect(screen.getByTestId("health-unknown").textContent).toContain("1");
  });

  it("近期 run 成功率:8/10 成功 = 80%,活跃(run.status 叠加)不计入且单独标注", async () => {
    const history: RunRecord[] = [];
    for (let i = 0; i < 8; i += 1) history.push(fixtureHistoryRun({}));
    history.push(fixtureHistoryRun({ status: "partial" }));
    history.push(fixtureHistoryRun({ status: "failed" }));
    history.push(fixtureHistoryRun({ status: "running" })); // pipeline start_run 即写表
    // 协议契约:runs.list 新→旧(entry.py `_m_runs_list`),夹具反转为真实顺序;
    // 注册表活跃条目与之合并 → running 表行标 active(mergeDashboardRuns ①)
    mockSidecar(
      Promise.resolve(fixtureDoctor()),
      Promise.resolve({ runs: history.reverse() }),
      Promise.resolve({ runs: [fixtureRegistryRun()] }),
    );
    render(<DashboardScreen />);

    await screen.findByText("科技资讯");
    expect(screen.getByTestId("run-success-rate").textContent).toBe("80%");
    expect(screen.getByTestId("run-success-rate").parentElement?.textContent).toContain("8/10 次成功");
    expect(screen.getByTestId("run-success-rate").parentElement?.textContent).toContain("1 个运行中");
    // 最近列表最多 10 条,且含最新(运行中)那条
    expect(screen.getByTestId(`recent-run-${nextRunId}`)).toBeTruthy();
    expect(screen.getAllByTestId(/^recent-run-/)).toHaveLength(10);
  });

  it("runs.list 历史可达:注册表空(sidecar 重启后)历史行仍上屏", async () => {
    // C3 验收的组件级形态:run.status 空(内存态随重启蒸发),仪表盘吃 runs.list
    mockSidecar(
      Promise.resolve(fixtureDoctor()),
      Promise.resolve({
        runs: [fixtureHistoryRun(), fixtureHistoryRun({ status: "cancelled" })], // 新→旧
      }),
      Promise.resolve({ runs: [] }),
    );
    render(<DashboardScreen />);

    await screen.findByText("科技资讯");
    expect(screen.getByTestId("run-success-rate").textContent).toBe("50%");
    expect(screen.getByTestId(`recent-run-${nextRunId}`).textContent).toContain("已取消");
  });

  it("sidecar 结构化错误上屏:错误码 + 中文原因,不裸崩", async () => {
    mockSidecar(
      Promise.reject(
        new SidecarRequestError({
          code: "config",
          path: "params.yamls",
          message: "品类 YAML 校验失败",
        }),
      ),
      Promise.resolve({ runs: [] }),
    );
    render(<DashboardScreen />);

    const banner = await screen.findByTestId("dashboard-error");
    expect(banner.textContent).toContain("config");
    expect(banner.textContent).toContain("品类 YAML 校验失败");
  });

  it("空态:无品类、无 run 时给引导文案,成功率为 —(不虚构 0%)", async () => {
    mockSidecar(Promise.resolve(fixtureDoctor({ plugins: [] })), Promise.resolve({ runs: [] }));
    render(<DashboardScreen />);

    await screen.findByText("暂无品类");
    expect(screen.getByText(/还没有 run 记录/)).toBeTruthy();
    expect(screen.getByTestId("run-success-rate").textContent).toBe("—");
  });

  it("刷新按钮重新拉取 doctor + run.status", async () => {
    mockSidecar(Promise.resolve(fixtureDoctor()), Promise.resolve({ runs: [] }));
    render(<DashboardScreen />);
    await screen.findByText("科技资讯");
    expect(doctorMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await screen.findByText("科技资讯");
    expect(doctorMock).toHaveBeenCalledTimes(2);
    expect(runStatusMock).toHaveBeenCalledTimes(2);
  });

  // -------------------------------------------------------------------------
  // G4(10-03-feed-ux):品类卡「跑一次」状态机(照抄 feed 空态 CTA)
  // -------------------------------------------------------------------------

  it("跑一次:run.start(yaml)→ completed 事件 → 刷新;busy 期按钮禁用", async () => {
    mockSidecar(Promise.resolve(fixtureDoctor()), Promise.resolve({ runs: [] }));
    runStartMock.mockResolvedValue({
      run_id: 7, state: "running", yaml: "tech.yaml", dry: false, db: "myia.db",
    });
    let emitEvent: ((event: { type: string; run_id: number }) => void) | undefined;
    onSidecarEventMock.mockImplementation((handler: (event: never) => void) => {
      emitEvent = handler as (event: { type: string; run_id: number }) => void;
      return Promise.resolve(() => {});
    });
    render(<DashboardScreen />);
    await screen.findByText("科技资讯");

    const runOnce = screen.getByRole("button", { name: "跑一次:科技资讯" });
    fireEvent.click(runOnce);
    await waitFor(() => expect(runStartMock).toHaveBeenCalledWith({ yaml: "tech.yaml" }));
    // busy(collecting)期按钮禁用,防重复发起
    await waitFor(() =>
      expect((screen.getByRole("button", { name: "跑一次:科技资讯" }) as HTMLButtonElement).disabled).toBe(true),
    );

    const doctorCallsBefore = doctorMock.mock.calls.length;
    act(() => emitEvent?.({ type: "completed", run_id: 7 }));
    await waitFor(() => expect(doctorMock.mock.calls.length).toBeGreaterThan(doctorCallsBefore));
    await waitFor(() =>
      expect((screen.getByRole("button", { name: "跑一次:科技资讯" }) as HTMLButtonElement).disabled).toBe(false),
    );
  });

  it("跑一次错误路径:run_busy 结构化拒绝行内回显,按钮回可用", async () => {
    mockSidecar(Promise.resolve(fixtureDoctor()), Promise.resolve({ runs: [] }));
    runStartMock.mockRejectedValue(
      new SidecarRequestError({ code: "run_busy", path: "$", message: "已有 run 在执行" }),
    );
    onSidecarEventMock.mockResolvedValue(() => {});
    render(<DashboardScreen />);
    await screen.findByText("科技资讯");

    fireEvent.click(screen.getByRole("button", { name: "跑一次:科技资讯" }));
    const errorLine = await screen.findByTestId("run-once-error-tech.yaml");
    expect(errorLine.textContent).toContain("run_busy");
    expect((screen.getByRole("button", { name: "跑一次:科技资讯" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
