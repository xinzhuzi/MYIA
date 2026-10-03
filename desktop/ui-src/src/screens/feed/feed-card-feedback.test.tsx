// @vitest-environment jsdom
/**
 * 情报流卡片反馈测试(B2,10-03-v112-desktop-parity):👍/👎 → feedback.mark
 * (dedup_key 身份);已标置灰防重(手动标记无服务端幂等);失败如实回显;
 * 仪表盘反馈统计卡空态与计数回显。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, feedbackMark: vi.fn(), feedbackStats: vi.fn() },
    onSidecarEvent: vi.fn(),
  };
});

const { api } = await import("@/lib/api");
const feedbackMarkMock = vi.mocked(api.feedbackMark);
const feedbackStatsMock = vi.mocked(api.feedbackStats);

import { FeedCardFeedback } from "./feed-card-feedback";
import { FeedbackStatsCard } from "@/screens/dashboard/feedback-stats-card";
import type { FeedItem } from "@/lib/api";

function fixtureItem(overrides: Partial<FeedItem> = {}): FeedItem {
  return {
    id: 42,
    url: "https://example.com/a",
    dedup_key: "sha256:abc",
    title: "标题",
    source: "src",
    content: null,
    tags: [],
    category: "tech",
    scores: null,
    pushed_at: null,
    push_slot: null,
    first_seen: "2026-10-03T08:00:00Z",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("FeedCardFeedback(B2 卡片标记)", () => {
  it("点 👍 → feedback.mark(dedup_key 身份,verdict=good);成功后双键置灰", async () => {
    feedbackMarkMock.mockResolvedValue({
      feedback_id: 7,
      item_id: 42,
      dedup_key: "sha256:abc",
      verdict: "good",
      channel: "desktop",
    });
    render(<FeedCardFeedback item={fixtureItem()} />);

    fireEvent.click(screen.getByLabelText("好评"));
    await waitFor(() => expect(feedbackMarkMock).toHaveBeenCalledWith({ item: "sha256:abc", verdict: "good" }));
    // 已标置灰(UI 防重:record_feedback 手动标记无幂等键;本项目无 jest-dom,原生直查)
    await waitFor(() => expect((screen.getByLabelText("好评") as HTMLButtonElement).disabled).toBe(true));
    expect((screen.getByLabelText("差评") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByLabelText("好评").getAttribute("aria-pressed")).toBe("true");
  });

  it("点 👎 → verdict=bad;标记态高亮差评", async () => {
    feedbackMarkMock.mockResolvedValue({
      feedback_id: 8,
      item_id: 42,
      dedup_key: "sha256:abc",
      verdict: "bad",
      channel: "desktop",
    });
    render(<FeedCardFeedback item={fixtureItem()} />);

    fireEvent.click(screen.getByLabelText("差评"));
    await waitFor(() => expect(feedbackMarkMock).toHaveBeenCalledWith({ item: "sha256:abc", verdict: "bad" }));
    await waitFor(() => expect(screen.getByLabelText("差评").getAttribute("aria-pressed")).toBe("true"));
  });

  it("mark 失败(code 透传)不置灰;行内回显失败提示", async () => {
    const { SidecarRequestError } = await import("@/lib/api");
    feedbackMarkMock.mockRejectedValue(
      new SidecarRequestError({ code: "item_not_found", path: "params.item", message: "条目不存在" }),
    );
    render(<FeedCardFeedback item={fixtureItem()} />);

    fireEvent.click(screen.getByLabelText("好评"));
    await waitFor(() => expect(screen.getByText("反馈失败")).toBeTruthy());
    expect((screen.getByLabelText("好评") as HTMLButtonElement).disabled).toBe(false); // 失败可重试
  });
});

describe("FeedbackStatsCard(B2 统计卡,仪表盘)", () => {
  it("零反馈 = 空态引导(不伪造 0%)", async () => {
    feedbackStatsMock.mockResolvedValue({
      window_days: 14,
      stats: {
        total: 0,
        good: 0,
        bad: 0,
        bad_ratio: 0,
        by_channel: {},
        top_bad_categories: [],
        top_bad_words: [],
      },
      active_tuning: {},
      tuning_history: [],
    });
    render(<FeedbackStatsCard />);
    await waitFor(() => expect(screen.getByTestId("feedback-stats-empty")).toBeTruthy());
  });

  it("有反馈:好/坏计数 + 差评率 + Top 负反馈类目摘要", async () => {
    feedbackStatsMock.mockResolvedValue({
      window_days: 14,
      stats: {
        total: 12,
        good: 8,
        bad: 4,
        bad_ratio: 0.3333,
        by_channel: { desktop: 12 },
        top_bad_categories: [
          { key: "tech", bad: 3 },
          { key: "stocks", bad: 1 },
        ],
        top_bad_words: [],
      },
      active_tuning: {},
      tuning_history: [],
    });
    render(<FeedbackStatsCard />);
    await waitFor(() => expect(screen.getByTestId("feedback-good").textContent).toBe("8"));
    expect(screen.getByTestId("feedback-bad").textContent).toBe("4");
    expect(screen.getByText("tech ×3")).toBeTruthy();
    expect(screen.getByText("共 12 条 · 差评率 33%")).toBeTruthy();
  });

  it("feedback.stats 失败:结构化错误如实回显", async () => {
    const { SidecarRequestError } = await import("@/lib/api");
    feedbackStatsMock.mockRejectedValue(
      new SidecarRequestError({ code: "store_corrupt", path: "$", message: "库不可读" }),
    );
    render(<FeedbackStatsCard />);
    await waitFor(() =>
      expect(screen.getByTestId("feedback-stats-error").textContent).toContain("store_corrupt"),
    );
  });
});
