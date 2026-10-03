// @vitest-environment jsdom
/**
 * 采集量趋势测试(B4,10-03-v112-desktop-parity)。
 * 纯函数聚合是验收落点(PRD B4「vitest 覆盖其数据聚合」):
 * fillDailyCounts 补零天 / 窗口裁剪 / 空态;toSparklinePoints 归一与全零平线;
 * TrendCard 窗口切换重查(store.trend)与总/峰值回显。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, storeTrend: vi.fn() },
    onSidecarEvent: vi.fn(),
  };
});

const { api } = await import("@/lib/api");
const storeTrendMock = vi.mocked(api.storeTrend);

import { fillDailyCounts, shiftUtcDate, toSparklinePoints, TREND_WINDOW_DEFAULT } from "./api";
import { TrendCard } from "./trend-card";
import type { TrendDay } from "@/lib/api";

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("fillDailyCounts 聚合(B4 验收落点)", () => {
  it("补零天:稀疏行铺满窗口,缺数日 count=0,旧→新稳定输出", () => {
    const rows: TrendDay[] = [
      { date: "2026-10-03", count: 4 },
      { date: "2026-09-30", count: 1 },
    ];
    const filled = fillDailyCounts(rows, 7, "2026-10-03");
    expect(filled).toHaveLength(7);
    expect(filled.map((day) => day.date)).toEqual([
      "2026-09-27",
      "2026-09-28",
      "2026-09-29",
      "2026-09-30",
      "2026-10-01",
      "2026-10-02",
      "2026-10-03",
    ]);
    expect(filled.map((day) => day.count)).toEqual([0, 0, 0, 1, 0, 0, 4]);
  });

  it("窗口裁剪:窗口外行丢弃(更早日期不带入);非法日期行防御性忽略", () => {
    const rows: TrendDay[] = [
      { date: "2020-01-01", count: 99 },
      { date: "2026-10-02", count: 2 },
      { date: "not-a-date", count: 7 },
    ];
    const filled = fillDailyCounts(rows, 3, "2026-10-03");
    expect(filled.map((day) => day.count)).toEqual([0, 2, 0]);
  });

  it("空态 = 全零窗口(不是空数组;sparkline 需等长序列)", () => {
    const filled = fillDailyCounts([], 14, "2026-10-03");
    expect(filled).toHaveLength(14);
    expect(filled.every((day) => day.count === 0)).toBe(true);
  });

  it("days 非法(0/负/小数)= 空数组(防御,不抛)", () => {
    expect(fillDailyCounts([], 0, "2026-10-03")).toEqual([]);
    expect(fillDailyCounts([], -3, "2026-10-03")).toEqual([]);
    expect(fillDailyCounts([], 2.5, "2026-10-03")).toEqual([]);
  });

  it("shiftUtcDate:UTC 字符历法加减(跨月/跨年正确,不经本地时区)", () => {
    expect(shiftUtcDate("2026-10-03", -1)).toBe("2026-10-02");
    expect(shiftUtcDate("2026-10-01", -1)).toBe("2026-09-30");
    expect(shiftUtcDate("2026-01-01", -1)).toBe("2025-12-31");
    expect(shiftUtcDate("2026-02-28", 1)).toBe("2026-03-01"); // 2026 非闰年
    expect(shiftUtcDate("bad", 1)).toBe("bad");
  });
});

describe("toSparklinePoints", () => {
  it("按 max 归一:峰值贴上边、零贴下边,点数 = 输入长度", () => {
    const points = toSparklinePoints([0, 5, 10], 100, 50, 5).split(" ");
    expect(points).toHaveLength(3);
    const [, mid, top] = points.map((point) => point.split(",").map(Number));
    expect(mid[1]).toBe(30 - 5); // (5/10) 半程:pad + span*(1-0.5) = 5+40*0.5=25
    expect(top[1]).toBe(5); // 峰值贴 pad
  });

  it("全零 = 居中平线(不除零);空输入/过小画布 = 空串", () => {
    const flat = toSparklinePoints([0, 0, 0], 100, 50, 5).split(" ");
    expect(flat.every((point) => point.split(",")[1] === "25.0")).toBe(true);
    expect(toSparklinePoints([], 100, 50)).toBe("");
    expect(toSparklinePoints([1], 4, 4)).toBe("");
  });

  it("单点居中(x = width/2,不除零)", () => {
    expect(toSparklinePoints([7], 100, 50, 5)).toBe("50.0,5.0");
  });
});

describe("TrendCard(B4 卡组件)", () => {
  it("默认 14 天窗口取数;总/峰值回显", async () => {
    storeTrendMock.mockResolvedValue({
      days: [
        { date: "2026-10-02", count: 3 },
        { date: "2026-10-03", count: 5 },
      ],
    });
    render(<TrendCard />);
    await waitFor(() => expect(screen.getByTestId("trend-total").textContent).toContain("共 8 条"));
    expect(storeTrendMock).toHaveBeenCalledWith({ days: TREND_WINDOW_DEFAULT });
    expect(screen.getByTestId("trend-total").textContent).toContain("峰值 5 条/日");
    expect(screen.getByTestId("trend-sparkline")).toBeTruthy();
  });

  it("窗口切换 7/14/30 天即时重查 store.trend", async () => {
    storeTrendMock.mockResolvedValue({ days: [] });
    render(<TrendCard />);
    await waitFor(() => expect(storeTrendMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByLabelText("趋势窗口 30 天"));
    await waitFor(() =>
      expect(storeTrendMock).toHaveBeenLastCalledWith({ days: 30 }),
    );
  });

  it("store.trend 失败:结构化错误如实回显(不伪装空态)", async () => {
    storeTrendMock.mockRejectedValue(
      new (await import("@/lib/api")).SidecarRequestError({
        code: "store_corrupt",
        path: "$",
        message: "库不可读",
      }),
    );
    render(<TrendCard />);
    await waitFor(() => expect(screen.getByTestId("trend-error").textContent).toContain("store_corrupt"));
  });
});
