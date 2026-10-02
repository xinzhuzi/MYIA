// @vitest-environment jsdom
/**
 * 仪表盘组件测试 —— mock sidecar(vi.mock "@/lib/api" 的 api 门面,
 * doctor / run.status 返回夹具;错误用真实 SidecarRequestError 注入)。
 * 覆盖:品类状态卡(含载入失败) / 源健康度四态汇总 / 近期 run 成功率 / 错误与空态。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SidecarRequestError } from "@/lib/api";
import type { DoctorResult, RunEntry, SourceHealthState } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      doctor: vi.fn(),
      runStatus: vi.fn(),
    },
  };
});

const { api } = await import("@/lib/api");
const doctorMock = vi.mocked(api.doctor);
const runStatusMock = vi.mocked(api.runStatus);

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
function fixtureRun(overrides: Partial<RunEntry> = {}): RunEntry {
  nextRunId += 1;
  return {
    run_id: nextRunId,
    yaml: `/plugins/${overrides.record?.category ?? "tech"}.yaml`,
    db: "myia.db",
    dry: false,
    state: "done",
    exit_code: 0,
    status: "success",
    started_at: "2026-10-02T08:00:00+00:00",
    finished_at: "2026-10-02T08:00:01+00:00",
    duration_ms: 1200,
    record: {
      run_id: nextRunId,
      category: "tech",
      status: "success",
      started_at: null,
      finished_at: null,
      stats: { items_retained: 5 },
      steps: null,
      error: null,
    },
    ...overrides,
  };
}

function mockSidecar(doctor: Promise<DoctorResult>, runs: Promise<{ runs: RunEntry[] }>) {
  doctorMock.mockReturnValue(doctor);
  runStatusMock.mockReturnValue(runs);
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

  it("近期 run 成功率:8/10 成功 = 80%,运行中不计入且单独标注", async () => {
    const runs: RunEntry[] = [];
    for (let i = 0; i < 8; i += 1) runs.push(fixtureRun({}));
    runs.push(fixtureRun({ status: "partial", exit_code: 3 }));
    runs.push(fixtureRun({ status: "failed", exit_code: 2 }));
    runs.push(fixtureRun({ state: "running", exit_code: null, status: null, record: null }));
    // 协议契约:run.status 新→旧(entry.py `_m_run_status`),夹具反转为真实顺序
    mockSidecar(Promise.resolve(fixtureDoctor()), Promise.resolve({ runs: runs.reverse() }));
    render(<DashboardScreen />);

    await screen.findByText("科技资讯");
    expect(screen.getByTestId("run-success-rate").textContent).toBe("80%");
    expect(screen.getByTestId("run-success-rate").parentElement?.textContent).toContain("8/10 次成功");
    expect(screen.getByTestId("run-success-rate").parentElement?.textContent).toContain("1 个运行中");
    // 最近列表最多 10 条,且含最新(运行中)那条
    expect(screen.getByTestId(`recent-run-${nextRunId}`)).toBeTruthy();
    expect(screen.getAllByTestId(/^recent-run-/)).toHaveLength(10);
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
});
