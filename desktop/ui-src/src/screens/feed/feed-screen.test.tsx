// @vitest-environment jsdom
/**
 * 情报流组件测试 —— mock sidecar(vi.mock "@/lib/api" 的 api 门面,store.items
 * 返回夹具分页;错误用真实 SidecarRequestError 注入)。
 * 覆盖:卡片渲染(标题/来源/分类/score/时间) / 未读·星标·稍后读三态(本地持久)
 * / 游标分页加载与判停 / 结构化错误与空态。
 * D4 批(10-03-ui-deep-imitation):品类色条与未读 accent 竖条、分组时间轴、
 * hover 浮现操作簇(accent/50 行背景 + time 让位)、U 快捷键、贴形加载骨架、
 * 错误态重试;纯函数 groupFeedItems/categoryColor 边界单测。
 * fe-small-batch 批(10-03-fe-small-batch):G8 卡片「AI 摘要」(feed.enrich:
 * loading/done/cached/scores 并回/无配置 graceful)/ G9 批量标已读未读(计数
 * 同步 + localStorage)/ G12 沉淀为关键词(yaml.list/read/save 经 invoke mock,
 * mtime 乐观锁 + 手术内容断言)/ P2⑤ 搜索框焦点环归全局体系(无局部 ring 覆写)。
 */
import { cleanup, fireEvent, render, screen, waitFor, within, act } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CategoryFilterContext } from "@/components/layout/app-layout";

import { SidecarRequestError } from "@/lib/api";
import type {
  FeedEnrichResult,
  FeedExportResult,
  FeedItem,
  HealthResult,
  StoreItemsParams,
  StoreItemsResult,
} from "@/lib/api";

import { appendWatchlistKeyword, categoryColor, groupFeedItems, setMarkerBulk } from "./api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      storeItems: vi.fn(),
      health: vi.fn(),
      runStart: vi.fn(),
      feedExport: vi.fn(),
      feedEnrich: vi.fn(),
    },
    onSidecarEvent: vi.fn(),
  };
});

// G2/G3 的 Tauri 壳包:open(打开原文)/ save(导出对话框)均 mock(零真实动作);
// core 的 invoke = G12 yaml.* 屏私有封装的唯一传输面,按 method 分发夹具
vi.mock("@tauri-apps/plugin-shell", () => ({ open: vi.fn() }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ save: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

const { api, onSidecarEvent } = await import("@/lib/api");
const storeItemsMock = vi.mocked(api.storeItems);
const healthMock = vi.mocked(api.health);
const runStartMock = vi.mocked(api.runStart);
const feedExportMock = vi.mocked(api.feedExport);
const feedEnrichMock = vi.mocked(api.feedEnrich);
const onSidecarEventMock = vi.mocked(onSidecarEvent);
const shellOpenMock = vi.mocked((await import("@tauri-apps/plugin-shell")).open);
const dialogSaveMock = vi.mocked((await import("@tauri-apps/plugin-dialog")).save);
// invoke 分发 mock(untyped Mock,惯例同 settings.test.tsx 的 mocks.invoke)
const invokeMock = (await import("@tauri-apps/api/core")).invoke as unknown as import("vitest").Mock;

import { FeedScreen } from "./feed-screen";

/** 空流时 refresh 会追问 health(first_run 分叉);默认给「有插件」的最小应答 */
function healthResult(overrides: Partial<HealthResult> = {}): HealthResult {
  return {
    command: "list",
    plugins_dir: "/home/plugins",
    db: "/home/myia.db",
    store_error: null,
    plugins: [
      {
        file: "/home/plugins/ai-news.yaml",
        id: "ai-news",
        name: "AI资讯",
        schedule: "0 8,20 * * *",
        timezone: null,
        push_channels: [],
        loaded: true,
        load_errors: null,
        sources: [],
      },
    ],
    summary: { plugins: 1, sources: 0, ok: 0, degraded: 0, dead: 0, unknown: 0 },
    healthy: true,
    first_run: false,
    exit_code: 0,
    ...overrides,
  } as HealthResult;
}

function renderScreen() {
  return render(
    <MemoryRouter>
      <FeedScreen />
    </MemoryRouter>,
  );
}

/** C8 接线路径:AppLayout 形态(Routes → Outlet context 下发品类) */
function renderScreenWithCategory(category: string | null) {
  const context: CategoryFilterContext = { category };
  return render(
    <MemoryRouter initialEntries={["/feed"]}>
      <Routes>
        <Route path="/" element={<Outlet context={context} />}>
          <Route path="feed" element={<FeedScreen />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// 夹具(形状严格对齐 types.ts:FeedItem / StoreItemsResult)
// ---------------------------------------------------------------------------

let nextItemId = 0;
function fixtureItem(overrides: Partial<FeedItem> = {}): FeedItem {
  nextItemId += 1;
  return {
    id: nextItemId,
    url: `https://example.com/item-${nextItemId}`,
    dedup_key: `dk-${nextItemId}`,
    title: `条目 ${nextItemId}`,
    source: "Example",
    content: "净化摘要内容",
    tags: ["ai"],
    category: "tech",
    scores: { tech: 0.87 },
    pushed_at: null,
    push_slot: null,
    first_seen: new Date().toISOString(),
    ...overrides,
  };
}

function result(items: FeedItem[]): StoreItemsResult {
  return { db: "myia.db", count: items.length, items };
}

// G12 夹具:plugins 目录两个 YAML(一好一坏)+ 目标原文(流式 keywords)
const G12_FILES = [
  {
    file: "/home/plugins/ai-news.yaml",
    name: "ai-news.yaml",
    parse_ok: true,
    category_id: "ai-news",
    category_name: "AI资讯",
  },
  {
    file: "/home/plugins/broken.yaml",
    name: "broken.yaml",
    parse_ok: false,
    category_id: null,
    category_name: null,
  },
];
const G12_CONTENT = "id: ai-news\nname: AI资讯\nwatchlist:\n  keywords: [LLM, Agent]\n  mute: [广告]\n";

/**
 * G12 yaml.* 三方法分发 mock(yaml.list/read/save);返回 save 调用捕获数组。
 * saveImpl 注入失败路径(如 mtime_conflict 的 JSON 串拒绝,= Rust Err 形态)。
 */
function mockYamlSidecar(options: { readContent?: string; saveImpl?: () => Promise<unknown> } = {}) {
  const readContent = options.readContent ?? G12_CONTENT;
  const saves: { file: string; content: string; expected_mtime: number }[] = [];
  invokeMock.mockImplementation(async (_command: string, args?: { method?: string; params?: Record<string, unknown> }) => {
    const method = args?.method;
    const params = args?.params ?? {};
    if (method === "yaml.list") return { plugins_dir: "/home/plugins", files: G12_FILES };
    if (method === "yaml.read") return { file: params.file, content: readContent, mtime: 111 };
    if (method === "yaml.save") {
      saves.push({
        file: String(params.file),
        content: String(params.content),
        expected_mtime: params.expected_mtime as number,
      });
      if (options.saveImpl) return options.saveImpl();
      return {
        file: params.file,
        written: true as const,
        created: false,
        backed_up: "/home/plugins/ai-news.yaml.bak",
        mtime: 222,
        warnings: [],
      };
    }
    throw JSON.stringify({ code: "method_not_found", path: "$", message: `测试未 mock 方法:${method}` });
  });
  return saves;
}

/** 内存 Storage:node 25 + vitest 4 的 jsdom 环境下 window.localStorage 被
 *  Node 实验性全局(缺方法)遮蔽,用可工作的 stub 保证本地态路径真实走到。 */
function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (key: string) => (map.has(key) ? (map.get(key) as string) : null),
    key: (index: number) => Array.from(map.keys())[index] ?? null,
    removeItem: (key: string) => void map.delete(key),
    setItem: (key: string, value: string) => void map.set(key, String(value)),
  };
}
const localStorageStub = memoryStorage();

beforeEach(() => {
  vi.stubGlobal("localStorage", localStorageStub);
  localStorageStub.clear();
  healthMock.mockResolvedValue(healthResult());
  onSidecarEventMock.mockResolvedValue(() => {});
});

afterEach(() => {
  cleanup(); // vitest globals 关闭,RTL 自动清理不生效,须显式清理
  vi.unstubAllGlobals();
  vi.resetAllMocks();
  nextItemId = 0;
});

// ---------------------------------------------------------------------------
// 用例
// ---------------------------------------------------------------------------

describe("FeedScreen", () => {
  it("条目卡渲染:标题 / 来源 / 分类标签 / score 徽标 / 相对时间", async () => {
    storeItemsMock.mockResolvedValue(result([fixtureItem()]));
    renderScreen();

    await screen.findByText("条目 1");
    expect(screen.getByText(/Example/)).toBeTruthy();
    expect(screen.getByText("tech")).toBeTruthy();
    expect(screen.getByText("ai")).toBeTruthy();
    expect(screen.getByText("0.87")).toBeTruthy();
    expect(screen.getByText(/刚刚/)).toBeTruthy();
  });

  it("图析行:image_ocr 条目出「图」Badge + 单行摘要(空白压平),无图条目零渲染", async () => {
    const withImage = fixtureItem({ image_ocr: "GPT-5 发布会\n  截图里的 关键数字" });
    const withoutImage = fixtureItem();
    storeItemsMock.mockResolvedValue(result([withImage, withoutImage]));
    renderScreen();

    await screen.findByText("条目 1");
    // 有图条目:「图」Badge + 压平空白的单行摘要(10-03-vision-pipeline)
    const ocrRow = screen.getByTestId(`feed-image-ocr-${withImage.id}`);
    expect(ocrRow.textContent).toContain("图");
    expect(ocrRow.textContent).toContain("GPT-5 发布会 截图里的 关键数字");
    // 无图条目:零渲染变化 —— 无图析行、无「图」Badge
    expect(screen.queryByTestId(`feed-image-ocr-${withoutImage.id}`)).toBeNull();
    expect(screen.getAllByText("图")).toHaveLength(1);
  });

  it("图析行截断:超长 image_ocr 只出前 160 字符 + 省略号(title 属性留全文)", async () => {
    const long = "字".repeat(300);
    const withImage = fixtureItem({ image_ocr: long });
    storeItemsMock.mockResolvedValue(result([withImage]));
    renderScreen();

    await screen.findByText("条目 1");
    const ocrRow = screen.getByTestId(`feed-image-ocr-${withImage.id}`);
    expect(ocrRow.textContent).toContain(`${"字".repeat(160)}…`);
    expect(ocrRow.textContent).not.toContain(`${"字".repeat(161)}`);
    // title 属性保留原文,悬停可看全量(详情展开属 v2);经 title 精确锚定文本 span
    expect(screen.getByTitle(long).textContent).toContain(`${"字".repeat(160)}…`);
  });

  it("三态(本地):点标题记已读、星标与稍后读切换,过滤页签生效", async () => {
    const items = [fixtureItem(), fixtureItem(), fixtureItem()];
    storeItemsMock.mockResolvedValue(result(items));
    renderScreen();
    await screen.findByText("条目 1");

    // 点标题 → 已读 → 默认「未读」过滤下消失
    fireEvent.click(screen.getByText("条目 1"));
    await waitFor(() => expect(screen.queryByText("条目 1")).toBeNull());
    expect(screen.getByText("2 / 3 条")).toBeTruthy();

    // 星标 item2 → 卡内星形按钮按下,localStorage 持久
    const card2 = screen.getByTestId(`feed-item-${items[1].id}`);
    fireEvent.click(within(card2).getByRole("button", { name: "星标" }));
    await waitFor(() =>
      expect(within(card2).getByRole("button", { name: "星标" }).getAttribute("aria-pressed")).toBe("true"),
    );
    const persisted: unknown = JSON.parse(localStorageStub.getItem("myia.feed.states.v1") ?? "{}");
    expect((persisted as Record<string, { starred?: boolean }>)[items[1].dedup_key]?.starred).toBe(true);

    // 稍后读 item3
    const card3 = screen.getByTestId(`feed-item-${items[2].id}`);
    fireEvent.click(within(card3).getByRole("button", { name: "稍后读" }));

    // 过滤页签:星标只显示 item2;稍后读只显示 item3
    fireEvent.click(screen.getByRole("button", { name: "过滤:星标" }));
    expect(screen.getByText("条目 2")).toBeTruthy();
    expect(screen.queryByText("条目 3")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "过滤:稍后读" }));
    expect(screen.getByText("条目 3")).toBeTruthy();
    expect(screen.queryByText("条目 2")).toBeNull();
  });

  it("重挂载后本地态仍在(localStorage 持久)", async () => {
    const item = fixtureItem();
    storeItemsMock.mockResolvedValue(result([item]));
    const { unmount } = renderScreen();
    await screen.findByText("条目 1");
    const card = screen.getByTestId(`feed-item-${item.id}`);
    fireEvent.click(within(card).getByRole("button", { name: "星标" }));
    unmount();

    renderScreen();
    const cardAgain = await screen.findByTestId(`feed-item-${item.id}`);
    expect(within(cardAgain).getByRole("button", { name: "星标" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("分页加载:首页 50 条出「加载更早」按钮,翻页带 before/before_id 复合游标,取尽后按钮消失", async () => {
    // 页 1:50 条(ids 100..51,新→旧);页 2:30 条(ids 50..21)
    const page1: FeedItem[] = [];
    for (let id = 100; id >= 51; id -= 1) {
      page1.push(fixtureItem({ id, dedup_key: `dk-${id}`, title: `条目 ${id}` }));
    }
    const oldest = page1[page1.length - 1];
    const cursor = oldest.first_seen as string;
    const cursorId = oldest.id as number;
    const page2: FeedItem[] = [];
    for (let id = 50; id >= 21; id -= 1) {
      page2.push(fixtureItem({ id, dedup_key: `dk-${id}`, title: `条目 ${id}` }));
    }
    storeItemsMock.mockImplementation((params?: StoreItemsParams) => {
      if (params?.before) return Promise.resolve(result(page2));
      return Promise.resolve(result(page1));
    });
    renderScreen();

    const loadMore = await screen.findByRole("button", { name: "加载更早的条目" });
    expect(screen.getByText("50 / 50 条")).toBeTruthy(); // 默认「未读」过滤:计数 50/50
    fireEvent.click(loadMore);

    // 翻页请求带上页最旧条目的 (first_seen, id) 复合游标(C1:同刻条目也能推进)
    await waitFor(() => expect(storeItemsMock).toHaveBeenCalledTimes(2));
    expect(storeItemsMock).toHaveBeenNthCalledWith(1, { limit: 50 });
    expect(storeItemsMock).toHaveBeenNthCalledWith(2, { limit: 50, before: cursor, before_id: cursorId });
    await screen.findByText("条目 21");
    expect(screen.getByText("80 / 80 条")).toBeTruthy();
    // 页 2 只有 30 条 < limit → 取尽,按钮消失
    expect(screen.queryByRole("button", { name: "加载更早的条目" })).toBeNull();
  });

  it("分页判停:翻页全为已加载条目(游标停滞)时按钮消失,且不出现重复卡", async () => {
    // 防御路径:服务端把已加载条目原样再回一遍(复合游标下不应发生,保留兜底防死循环)
    const page1: FeedItem[] = [];
    for (let id = 50; id >= 1; id -= 1) {
      page1.push(fixtureItem({ id, dedup_key: `dk-${id}`, title: `条目 ${id}` }));
    }
    storeItemsMock.mockImplementation(() => Promise.resolve(result(page1)));
    renderScreen();
    const loadMore = await screen.findByRole("button", { name: "加载更早的条目" });
    fireEvent.click(loadMore);

    await waitFor(() => expect(storeItemsMock).toHaveBeenCalledTimes(2));
    // 追加 0 条 → 判停按钮消失;仍是 50 张卡、无重复
    expect(screen.queryByRole("button", { name: "加载更早的条目" })).toBeNull();
    expect(screen.getAllByTestId(/^feed-item-/)).toHaveLength(50);
  });

  it("sidecar 结构化错误上屏(store_corrupt)", async () => {
    storeItemsMock.mockRejectedValue(
      new SidecarRequestError({ code: "store_corrupt", path: "params.db", message: "库文件损坏" }),
    );
    renderScreen();

    const banner = await screen.findByTestId("feed-error");
    expect(banner.textContent).toContain("store_corrupt");
    expect(banner.textContent).toContain("库文件损坏");
  });

  it("空态:无条目给「运行第一个插件」CTA(默认未读页签同口径)", async () => {
    storeItemsMock.mockResolvedValue(result([]));
    renderScreen();

    expect(await screen.findByText("情报流还是空的")).toBeTruthy();
    expect(screen.getByRole("button", { name: /运行第一个插件/ })).toBeTruthy();
  });

  it("空态 first_run 分支:无插件给初始化引导 + 去源管理,无运行 CTA", async () => {
    storeItemsMock.mockResolvedValue(result([]));
    healthMock.mockResolvedValue(healthResult({ first_run: true, plugins: [] }));
    renderScreen();

    expect(await screen.findByText("还没有可运行的插件")).toBeTruthy();
    expect(screen.getByRole("button", { name: "去源管理" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /运行第一个插件/ })).toBeNull();
  });

  it("空流 CTA:点击 → health 取已加载插件 → run.start(yaml) → 采集中 → completed 刷新", async () => {
    storeItemsMock.mockResolvedValue(result([]));
    runStartMock.mockResolvedValue({
      run_id: 3, state: "running", yaml: "/home/plugins/ai-news.yaml", dry: false, db: "/home/myia.db",
    });
    let emitEvent: ((event: { type: string; run_id: number }) => void) | undefined;
    onSidecarEventMock.mockImplementation((handler: (event: never) => void) => {
      emitEvent = handler as (event: { type: string; run_id: number }) => void;
      return Promise.resolve(() => {});
    });
    renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: /运行第一个插件/ }));
    await waitFor(() =>
      expect(runStartMock).toHaveBeenCalledWith({ yaml: "/home/plugins/ai-news.yaml" }),
    );
    expect(await screen.findByText(/采集中\(run #3\)/)).toBeTruthy();

    // completed 事件 → done 文案 + 刷新(store.items 再被调用)
    const callsBefore = storeItemsMock.mock.calls.length;
    act(() => emitEvent?.({ type: "completed", run_id: 3 }));
    expect(await screen.findByText(/本次采集已结束/)).toBeTruthy();
    await waitFor(() => expect(storeItemsMock.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  it("空流 CTA 错误路径:run_busy 结构化拒绝上屏,按钮可重试", async () => {
    storeItemsMock.mockResolvedValue(result([]));
    runStartMock.mockRejectedValue(
      new SidecarRequestError({ code: "run_busy", path: "$", message: "已有 run 在执行" }),
    );
    renderScreen();

    fireEvent.click(await screen.findByRole("button", { name: /运行第一个插件/ }));
    expect(await screen.findByText(/run_busy/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /运行第一个插件/ })).toBeTruthy();
  });

  // -------------------------------------------------------------------------
  // feed-ux 批(10-03-feed-ux):C8 品类接线 / G1 搜索 / G2 展开与打开原文 / G3 导出
  // -------------------------------------------------------------------------

  it("C8 品类接线:Outlet context 品类 → storeItems 收到 category 服务端过滤;null = 不传参", async () => {
    storeItemsMock.mockResolvedValue(result([fixtureItem()]));
    renderScreenWithCategory("ai-news");
    await screen.findByText("条目 1");
    expect(storeItemsMock).toHaveBeenCalledWith({ limit: 50, category: "ai-news" });
  });

  it("G1 搜索:Enter 即时提交 → query 随首页与翻页透传;计数行双层如实", async () => {
    storeItemsMock.mockResolvedValue(result([fixtureItem()]));
    renderScreen();
    await screen.findByText("条目 1");

    fireEvent.change(screen.getByLabelText("搜索条目"), { target: { value: "GLM" } });
    fireEvent.keyDown(screen.getByLabelText("搜索条目"), { key: "Enter" });

    await waitFor(() => expect(storeItemsMock).toHaveBeenLastCalledWith({ limit: 50, query: "GLM" }));
    expect(await screen.findByTestId("feed-search-scope")).toBeTruthy();
    expect(screen.getByTestId("feed-search-scope").textContent).toContain("GLM");
  });

  it("G1 搜索空态:零命中给专属空态文案(不再误导为「情报流是空的」)", async () => {
    storeItemsMock.mockResolvedValue(result([]));
    renderScreen();
    fireEvent.change(screen.getByLabelText("搜索条目"), { target: { value: "不存在的词" } });
    fireEvent.keyDown(screen.getByLabelText("搜索条目"), { key: "Enter" });

    expect(await screen.findByTestId("feed-search-empty")).toBeTruthy();
    expect(screen.queryByText("情报流还是空的")).toBeNull();
  });

  it("G2 卡片展开:展开按钮出全文与元信息,再点收起回两行摘要", async () => {
    const item = fixtureItem({ content: "第一行\n第二行\n第三行" });
    storeItemsMock.mockResolvedValue(result([item]));
    renderScreen();
    await screen.findByText("条目 1");

    const card = screen.getByTestId(`feed-item-${item.id}`);
    const expandButton = within(card).getByRole("button", { name: "展开条目" });
    expect(expandButton.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId(`feed-expanded-${item.id}`)).toBeNull(); // 收起态无全文块

    fireEvent.click(expandButton);
    const expanded = await screen.findByTestId(`feed-expanded-${item.id}`);
    expect(expanded.textContent).toContain("第一行");
    expect(within(card).getByRole("button", { name: "收起条目" }).getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(within(card).getByRole("button", { name: "收起条目" }));
    await waitFor(() => expect(screen.queryByTestId(`feed-expanded-${item.id}`)).toBeNull());
  });

  it("G2 打开原文:http(s) 条目出按钮并调 plugin-shell open;非 http(s) 不渲染按钮", async () => {
    const httpItem = fixtureItem({ url: "https://example.com/story" });
    const ftpItem = fixtureItem({ url: "ftp://files.example.com/x" });
    storeItemsMock.mockResolvedValue(result([httpItem, ftpItem]));
    renderScreen();
    await screen.findByText("条目 1");

    const httpCard = screen.getByTestId(`feed-item-${httpItem.id}`);
    fireEvent.click(within(httpCard).getByRole("button", { name: "打开原文" }));
    await waitFor(() => expect(shellOpenMock).toHaveBeenCalledWith("https://example.com/story"));

    const ftpCard = screen.getByTestId(`feed-item-${ftpItem.id}`);
    expect(within(ftpCard).queryByRole("button", { name: "打开原文" })).toBeNull();
  });

  it("G2 打开原文失败:错误回显在 feed-open-error 行", async () => {
    shellOpenMock.mockRejectedValue(new Error("shell 未授权"));
    const item = fixtureItem({ url: "https://example.com/story" });
    storeItemsMock.mockResolvedValue(result([item]));
    renderScreen();
    await screen.findByText("条目 1");

    fireEvent.click(within(screen.getByTestId(`feed-item-${item.id}`)).getByRole("button", { name: "打开原文" }));
    expect(await screen.findByTestId("feed-open-error")).toBeTruthy();
    expect(screen.getByTestId("feed-open-error").textContent).toContain("shell 未授权");
  });

  it("G3 导出:选格式 → dialog.save 默认名带日期 → feed.export 带当前过滤 → 回显 path/count", async () => {
    const item = fixtureItem();
    storeItemsMock.mockResolvedValue(result([item]));
    renderScreen();
    await screen.findByText("条目 1");

    dialogSaveMock.mockResolvedValue("/tmp/myia-feed-export.jsonl");
    const exportResult: FeedExportResult = { path: "/tmp/myia-feed-export.jsonl", count: 1, bytes: 640 };
    feedExportMock.mockResolvedValue(exportResult);

    fireEvent.click(screen.getByRole("button", { name: "导出当前视图" }));
    await waitFor(() => expect(feedExportMock).toHaveBeenCalled());
    expect(feedExportMock).toHaveBeenCalledWith({ format: "jsonl", path: "/tmp/myia-feed-export.jsonl" });
    expect(dialogSaveMock).toHaveBeenCalledWith(
      expect.objectContaining({ defaultPath: expect.stringMatching(/^myia-feed-\d{8}\.jsonl$/) }),
    );
    expect(await screen.findByTestId("feed-export-result")).toBeTruthy();
    expect(screen.getByTestId("feed-export-result").textContent).toContain("/tmp/myia-feed-export.jsonl");
    expect(screen.getByTestId("feed-export-result").textContent).toContain("1 条");

    // CSV 格式切换后走 csv 词表
    fireEvent.click(screen.getByRole("button", { name: "CSV" }));
    dialogSaveMock.mockResolvedValue("/tmp/myia-feed-export.csv");
    feedExportMock.mockResolvedValue({ path: "/tmp/myia-feed-export.csv", count: 1, bytes: 120 });
    fireEvent.click(screen.getByRole("button", { name: "导出当前视图" }));
    await waitFor(() =>
      expect(feedExportMock).toHaveBeenLastCalledWith({ format: "csv", path: "/tmp/myia-feed-export.csv" }),
    );
  });

  it("G3 导出:对话框取消 = 静默(零 RPC 零回显);export_path_invalid 错误回显", async () => {
    storeItemsMock.mockResolvedValue(result([fixtureItem()]));
    renderScreen();
    await screen.findByText("条目 1");

    dialogSaveMock.mockResolvedValue(null);
    fireEvent.click(screen.getByRole("button", { name: "导出当前视图" }));
    await waitFor(() => expect(dialogSaveMock).toHaveBeenCalled());
    expect(feedExportMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("feed-export-result")).toBeNull();

    dialogSaveMock.mockResolvedValue("/tmp/again.jsonl");
    feedExportMock.mockRejectedValue(
      new SidecarRequestError({ code: "export_write_failed", path: "params.path", message: "磁盘满" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "导出当前视图" }));
    expect(await screen.findByTestId("feed-export-result")).toBeTruthy();
    expect(screen.getByTestId("feed-export-result").textContent).toContain("export_write_failed");
  });

  // -------------------------------------------------------------------------
  // vision-v2 批(10-03-vision-v2):图析详情展开(OCR 全文/逐行置信度/caption/图文件)
  // -------------------------------------------------------------------------

  it("v2 图析详情展开:OCR 全文等宽块 + 逐行置信度表 + caption 全文 + 图文件路径列表;收起回单行摘要", async () => {
    const item = fixtureItem({
      content: null,
      image_ocr: "第一段落\n第二段落",
      image_ocr_lines: [
        { text: "行一(高置信)", conf: 0.95 },
        { text: "行二(低置信)", conf: 0.5 },
      ],
      image_caption: "一张发布会舞台照片,大屏写着发布日期。",
      image_files: ["/data/images/ab/cd12.jpg"],
    });
    storeItemsMock.mockResolvedValue(result([item]));
    renderScreen();
    await screen.findByText("条目 1");

    const card = screen.getByTestId(`feed-item-${item.id}`);
    // 无正文但有图析 → 仍可展开(hasImageDetails)
    const expandButton = within(card).getByRole("button", { name: "展开条目" });
    // 收起态:只有单行摘要(压平),零详情块
    expect(screen.getByTestId(`feed-image-ocr-${item.id}`).textContent).toContain("第一段落 第二段落");
    expect(screen.queryByTestId(`feed-image-ocr-lines-${item.id}`)).toBeNull();
    expect(screen.queryByTestId(`feed-image-caption-${item.id}`)).toBeNull();
    expect(screen.queryByTestId(`feed-image-files-${item.id}`)).toBeNull();

    fireEvent.click(expandButton);

    // OCR 全文:等宽块保换行(摘要态的压平不复现)
    const ocrFull = await screen.findByTestId(`feed-image-ocr-${item.id}`);
    expect(ocrFull.textContent).toContain("OCR 全文");
    const ocrPre = ocrFull.querySelector("pre");
    expect(ocrPre).not.toBeNull();
    expect(ocrPre?.textContent).toBe("第一段落\n第二段落"); // 换行原样保留

    // 逐行表:文本 + 置信度百分比(0-1 → %)
    const lines = screen.getByTestId(`feed-image-ocr-lines-${item.id}`);
    expect(lines.textContent).toContain("行一(高置信)");
    expect(lines.textContent).toContain("行二(低置信)");
    expect(lines.textContent).toContain("95%");
    expect(lines.textContent).toContain("50%");

    // caption 全文
    expect(screen.getByTestId(`feed-image-caption-${item.id}`).textContent).toContain("一张发布会舞台照片,大屏写着发布日期。");

    // 图文件:「图文件 N 张(路径)」文本列表(v2.2 再做图片本尊)
    const files = screen.getByTestId(`feed-image-files-${item.id}`);
    expect(files.textContent).toContain("图文件 1 张");
    expect(files.textContent).toContain("/data/images/ab/cd12.jpg");

    // 收起 → 回单行摘要,详情块消失
    fireEvent.click(within(card).getByRole("button", { name: "收起条目" }));
    await waitFor(() => expect(screen.queryByTestId(`feed-image-ocr-lines-${item.id}`)).toBeNull());
    expect(screen.getByTestId(`feed-image-ocr-${item.id}`).textContent).toContain("第一段落 第二段落");
  });

  it("v2 无图析条目:无正文且无图析 → 无展开按钮;有正文无图析 → 展开只显正文零图析块", async () => {
    const bare = fixtureItem({ content: null });
    const textOnly = fixtureItem({ content: "纯文本摘要" });
    storeItemsMock.mockResolvedValue(result([bare, textOnly]));
    renderScreen();
    await screen.findByText("条目 1");

    const bareCard = screen.getByTestId(`feed-item-${bare.id}`);
    expect(within(bareCard).queryByRole("button", { name: "展开条目" })).toBeNull();

    const textCard = screen.getByTestId(`feed-item-${textOnly.id}`);
    fireEvent.click(within(textCard).getByRole("button", { name: "展开条目" }));
    await screen.findByTestId(`feed-expanded-${textOnly.id}`);
    expect(screen.queryByTestId(`feed-image-ocr-lines-${textOnly.id}`)).toBeNull();
    expect(screen.queryByTestId(`feed-image-caption-${textOnly.id}`)).toBeNull();
    expect(screen.queryByTestId(`feed-image-files-${textOnly.id}`)).toBeNull();
    expect(screen.queryByTestId(`feed-image-ocr-${textOnly.id}`)).toBeNull();
  });

  it("v2 仅图析单键也能展开:image_ocr_lines 独有(无 ocr 全文)时逐行表可见", async () => {
    const item = fixtureItem({
      content: null,
      image_ocr_lines: [{ text: "仅逐行", conf: 0.88 }],
    });
    storeItemsMock.mockResolvedValue(result([item]));
    renderScreen();
    await screen.findByText("条目 1");

    const card = screen.getByTestId(`feed-item-${item.id}`);
    fireEvent.click(within(card).getByRole("button", { name: "展开条目" }));
    const lines = await screen.findByTestId(`feed-image-ocr-lines-${item.id}`);
    expect(lines.textContent).toContain("仅逐行");
    expect(lines.textContent).toContain("88%");
    // 无 image_ocr → 摘要行/全文块零渲染
    expect(screen.queryByTestId(`feed-image-ocr-${item.id}`)).toBeNull();
  });

  // -------------------------------------------------------------------------
  // D4 批(10-03-ui-deep-imitation):品类色条 / 分组时间轴 / hover 浮现 /
  // U 快捷键 / 三态(贴形骨架、错误重试);teardown-linear-activity #4/5/6/11
  // -------------------------------------------------------------------------

  it("D4 品类色条与未读竖条:未读 = accent 竖条,已读 = 品类色(inline),已读无品类零色件", async () => {
    // jsdom 把 inline 的 #rrggbb 规范化为 rgb() —— 断言前同法换算期望值
    const asRgb = (hex: string) => {
      const value = Number.parseInt(hex.slice(1), 16);
      return `rgb(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255})`;
    };
    const unread = fixtureItem({ category: "ai-news" });
    const read = fixtureItem({ category: "stocks" });
    const readNoCategory = fixtureItem({ category: null });
    localStorageStub.setItem(
      "myia.feed.states.v1",
      JSON.stringify({
        [read.dedup_key]: { read: true },
        [readNoCategory.dedup_key]: { read: true },
      }),
    );
    storeItemsMock.mockResolvedValue(result([unread, read, readNoCategory]));
    renderScreen();
    await screen.findByText("条目 1");
    fireEvent.click(screen.getByRole("button", { name: "过滤:全部" }));

    // 未读卡:data-unread=true,竖条 = accent(bg-primary),无 inline 色
    const unreadCard = screen.getByTestId(`feed-item-${unread.id}`);
    expect(unreadCard.getAttribute("data-unread")).toBe("true");
    const unreadStrip = screen.getByTestId(`feed-strip-${unread.id}`);
    expect(unreadStrip.className).toContain("bg-primary");
    expect(unreadStrip.style.backgroundColor).toBe("");

    // 已读卡:竖条 = 品类色(opacity-70 + inline backgroundColor)
    const readCard = screen.getByTestId(`feed-item-${read.id}`);
    expect(readCard.getAttribute("data-unread")).toBe("false");
    const readStrip = screen.getByTestId(`feed-strip-${read.id}`);
    expect(readStrip.className).toContain("opacity-70");
    expect(readStrip.style.backgroundColor).toBe(asRgb(categoryColor("stocks") as string));

    // 品类徽标着色同源(色条与徽标一色)
    const chip = screen.getByText("ai-news");
    expect(chip.style.color).toBe(asRgb(categoryColor("ai-news") as string));

    // 已读且无品类:零色件(无竖条)
    expect(screen.queryByTestId(`feed-strip-${readNoCategory.id}`)).toBeNull();
  });

  it("D4 hover 浮现操作:行背景 accent/50,操作簇 opacity-0→hover 显(focus-within 可达),time 让位", async () => {
    const item = fixtureItem({ url: "https://example.com/story" });
    storeItemsMock.mockResolvedValue(result([item]));
    renderScreen();
    const card = await screen.findByTestId(`feed-item-${item.id}`);

    // teardown #5:行 hover 背景 = accent/50
    expect(card.className).toContain("hover:bg-accent/50");
    // 操作簇:平时 opacity-0,hover/focus-within 显(浮现动效走 token)
    const actions = card.querySelector("[data-feed-actions]");
    expect(actions).not.toBeNull();
    expect(actions?.className).toContain("opacity-0");
    expect(actions?.className).toContain("group-hover/feed-item:opacity-100");
    expect(actions?.className).toContain("focus-within:opacity-100");
    // 操作簇内动作齐全(反馈/原文/星标/稍后读/已读)
    for (const name of ["好评", "差评", "打开原文", "星标", "稍后读", "标记已读"]) {
      expect(within(actions as HTMLElement).getByRole("button", { name })).toBeTruthy();
    }
    // 右对齐相对时间(teardown #4):独立 time 元素,hover 让位给操作簇
    const time = card.querySelector("time");
    expect(time?.className).toContain("text-2xs");
    expect(time?.className).toContain("group-hover/feed-item:opacity-0");
    expect(time?.textContent).toContain("刚刚");
  });

  it("D4 分组时间轴:今日条目落「今天」sticky 组头(计数如实,同组单头)", async () => {
    const items = [fixtureItem(), fixtureItem()];
    storeItemsMock.mockResolvedValue(result(items));
    renderScreen();

    const header = await screen.findByTestId("feed-group-今天");
    expect(header.textContent).toContain("今天");
    expect(header.textContent).toContain("2 条");
    expect(header.className).toContain("sticky");
    expect(screen.getAllByTestId("feed-group-今天")).toHaveLength(1);
  });

  it("D4 U 快捷键:hover 进入卡后按 U 切已读(未读过滤下离场);输入框内敲 u 不触发", async () => {
    const item = fixtureItem();
    const other = fixtureItem();
    storeItemsMock.mockResolvedValue(result([item, other]));
    renderScreen();
    await screen.findByText("条目 1");

    fireEvent.mouseEnter(screen.getByTestId(`feed-item-${item.id}`));
    fireEvent.keyDown(window, { key: "u" });
    // 当前卡转已读 → 默认「未读」过滤下消失;另一卡不受牵连
    await waitFor(() => expect(screen.queryByTestId(`feed-item-${item.id}`)).toBeNull());
    expect(screen.getByTestId(`feed-item-${other.id}`).getAttribute("data-current")).toBe("false");

    // 输入框内敲 u:守卫生效(另一张卡不被切已读)
    const search = screen.getByLabelText("搜索条目");
    fireEvent.change(search, { target: { value: "u" } });
    fireEvent.keyDown(search, { key: "u" });
    expect(screen.getByTestId(`feed-item-${other.id}`).getAttribute("data-unread")).toBe("true");
  });

  it("D4 加载态:贴形骨架(feed-loading)常驻至首页应答,应答后卸载", async () => {
    let resolveItems: ((value: StoreItemsResult) => void) | undefined;
    storeItemsMock.mockImplementation(
      () => new Promise<StoreItemsResult>((resolve) => {
        resolveItems = resolve;
      }),
    );
    renderScreen();

    expect(await screen.findByTestId("feed-loading")).toBeTruthy();
    expect(screen.queryByTestId(/^feed-item-/)).toBeNull();
    act(() => resolveItems?.(result([fixtureItem()])));
    await screen.findByTestId("feed-item-1");
    expect(screen.queryByTestId("feed-loading")).toBeNull();
  });

  it("D4 错误态:结构化错误卡带重试按钮,点击重发 store.items 并恢复", async () => {
    storeItemsMock.mockRejectedValueOnce(
      new SidecarRequestError({ code: "store_corrupt", path: "params.db", message: "库文件损坏" }),
    );
    storeItemsMock.mockResolvedValueOnce(result([fixtureItem()]));
    renderScreen();

    const banner = await screen.findByTestId("feed-error");
    expect(banner.textContent).toContain("store_corrupt");
    // 错误态与空态互斥(质检修):错误卡在场时不得再落入空态 CTA
    // (store 损坏下「运行第一个插件」必失败,两卡叠加语义矛盾)
    expect(screen.queryByTestId("feed-run-cta")).toBeNull();
    expect(screen.queryByText("情报流还是空的")).toBeNull();
    fireEvent.click(within(banner).getByRole("button", { name: "重试" }));
    await screen.findByTestId("feed-item-1");
    expect(screen.queryByTestId("feed-error")).toBeNull();
  });

  // -------------------------------------------------------------------------
  // fe-small-batch 批(10-03-fe-small-batch):G8 AI 摘要 / G9 批量 /
  // G12 沉淀为关键词 / P2⑤ 搜索框焦点环
  // -------------------------------------------------------------------------

  it("G8 AI 摘要:点击 → feed.enrich(dedup_key)→ 复合分/模型/维度分/缓存命中上屏,scores 并回刷新徽标", async () => {
    const item = fixtureItem({ scores: { tech: 0.87 } });
    storeItemsMock.mockResolvedValue(result([item]));
    renderScreen();
    await screen.findByText("条目 1");
    expect(screen.getByText("0.87")).toBeTruthy(); // 初始分数徽标

    feedEnrichMock.mockResolvedValue({
      item_id: item.id,
      model: "test-model",
      scores: { 相关性: 8, 新颖性: 6 },
      score: 7.2,
      cached: true,
    } as FeedEnrichResult);

    fireEvent.click(within(screen.getByTestId(`feed-item-${item.id}`)).getByRole("button", { name: "AI 摘要" }));
    expect(feedEnrichMock).toHaveBeenCalledWith({ item: item.dedup_key });

    const block = await screen.findByTestId(`feed-enrich-${item.id}`);
    expect(block.textContent).toContain("7.20");
    expect(block.textContent).toContain("test-model");
    expect(block.textContent).toContain("缓存命中");
    expect(block.textContent).toContain("相关性 8");
    // scores 并回本地列表:分数徽标 0.87 → 8.00(维度最大分)
    await waitFor(() => expect(screen.getByText("8.00")).toBeTruthy());
    expect(screen.queryByText("0.87")).toBeNull();
  });

  it("G8 loading 态:请求在途按钮禁用 + 「精评中…」行", async () => {
    const item = fixtureItem();
    storeItemsMock.mockResolvedValue(result([item]));
    renderScreen();
    await screen.findByText("条目 1");

    feedEnrichMock.mockImplementation(() => new Promise<FeedEnrichResult>(() => {}));
    const button = within(screen.getByTestId(`feed-item-${item.id}`)).getByRole("button", { name: "AI 摘要" });
    fireEvent.click(button);

    expect(await screen.findByTestId(`feed-enrich-loading-${item.id}`)).toBeTruthy();
    expect(button.getAttribute("disabled")).not.toBeNull();
  });

  it("G8 无配置 graceful:enrich_not_configured 明示「无精评配置」(不伪装成传输错误),按钮可重试", async () => {
    const item = fixtureItem();
    storeItemsMock.mockResolvedValue(result([item]));
    renderScreen();
    await screen.findByText("条目 1");

    feedEnrichMock.mockRejectedValue(
      new SidecarRequestError({
        code: "enrich_not_configured",
        path: "params.item",
        message: "无法精评:品类 'tech' 未启用 enrich",
        data: { category: "tech", reason: "enrich_disabled" },
      }),
    );
    const button = within(screen.getByTestId(`feed-item-${item.id}`)).getByRole("button", { name: "AI 摘要" });
    fireEvent.click(button);

    const error = await screen.findByTestId(`feed-enrich-error-${item.id}`);
    expect(error.textContent).toContain("无精评配置");
    expect(error.textContent).toContain("未启用 enrich");
    expect(error.getAttribute("title")).toContain("enrich_not_configured");
    await waitFor(() => expect(button.getAttribute("disabled")).toBeNull());
  });

  it("G9 全部标已读:一键生效 → 计数同步 0/3 + 本地持久;全部标未读可还原", async () => {
    const items = [fixtureItem(), fixtureItem(), fixtureItem()];
    storeItemsMock.mockResolvedValue(result(items));
    renderScreen();
    await screen.findByText("条目 1");
    expect(screen.getByText("3 / 3 条")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "全部标已读" }));

    // 默认「未读」过滤:计数 0/3、卡片全部离场、空态如实
    await waitFor(() => expect(screen.getByText("0 / 3 条")).toBeTruthy());
    expect(screen.queryByTestId(/^feed-item-/)).toBeNull();
    expect(screen.getByText("没有未读条目")).toBeTruthy();
    const persisted: unknown = JSON.parse(localStorageStub.getItem("myia.feed.states.v1") ?? "{}");
    for (const item of items) {
      expect((persisted as Record<string, { read?: boolean }>)[item.dedup_key]?.read).toBe(true);
    }

    fireEvent.click(screen.getByRole("button", { name: "全部标未读" }));
    await waitFor(() => expect(screen.getByText("3 / 3 条")).toBeTruthy());
    expect(screen.getAllByTestId(/^feed-item-/)).toHaveLength(3);
  });

  it("G9 空载时批量按钮禁用(无事可做不诱点击)", async () => {
    storeItemsMock.mockResolvedValue(result([]));
    renderScreen();
    await screen.findByText("情报流还是空的");
    expect(screen.getByRole("button", { name: "全部标已读" }).getAttribute("disabled")).not.toBeNull();
    expect(screen.getByRole("button", { name: "全部标未读" }).getAttribute("disabled")).not.toBeNull();
  });

  it("P2⑤:搜索框焦点环归全局 :focus-visible 体系(无局部 ring-1/focus-visible 覆写)", async () => {
    storeItemsMock.mockResolvedValue(result([fixtureItem()]));
    renderScreen();
    await screen.findByText("条目 1");
    const input = screen.getByLabelText("搜索条目");
    expect(input.className).not.toContain("ring-1");
    expect(input.className).not.toContain("focus-visible");
  });

  it("G12 沉淀为关键词:面板只列 parse_ok 目标,词面默认条目标题;read(mtime)→ 手术 → save → 回执带 .bak", async () => {
    const item = fixtureItem({ title: "GLM-5 发布" });
    storeItemsMock.mockResolvedValue(result([item]));
    renderScreen();
    await screen.findByText("GLM-5 发布");
    const card = screen.getByTestId(`feed-item-${item.id}`);

    const saves = mockYamlSidecar();
    fireEvent.click(within(card).getByRole("button", { name: "沉淀为关键词" }));

    const panel = await screen.findByTestId(`feed-keyword-pin-${item.id}`);
    // 目标下拉只列可解析文件(坏文件不给选:写入必过校验,不往死路上引)
    const select = within(panel).getByLabelText("目标品类 YAML") as HTMLSelectElement;
    const options = Array.from(select.querySelectorAll("option"));
    expect(options).toHaveLength(1);
    expect(options[0]?.textContent).toContain("ai-news.yaml");
    // 词面默认 = 条目标题(可改)
    expect((within(panel).getByLabelText("沉淀关键词") as HTMLInputElement).value).toBe("GLM-5 发布");

    fireEvent.click(within(panel).getByRole("button", { name: "写入" }));

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(invokeMock).toHaveBeenCalledWith("sidecar_request", {
      method: "yaml.read",
      params: { file: "/home/plugins/ai-news.yaml" },
    });
    // 手术内容:keywords 闭括号前追加新词,注释/其余行(含 id 行)逐字节不动;
    // expected_mtime = read 带回的乐观锁基线
    expect(saves[0]).toEqual({
      file: "/home/plugins/ai-news.yaml",
      content: "id: ai-news\nname: AI资讯\nwatchlist:\n  keywords: [LLM, Agent, GLM-5 发布]\n  mute: [广告]\n",
      expected_mtime: 111,
    });
    const note = await screen.findByTestId(`feed-keyword-note-${item.id}`);
    expect(note.textContent).toContain("已写入 ai-news.yaml");
    expect(note.textContent).toContain(".bak");
  });

  it("G12 词已在列表:already_present 零写入(save 不发),回执如实", async () => {
    const item = fixtureItem();
    storeItemsMock.mockResolvedValue(result([item]));
    renderScreen();
    await screen.findByText("条目 1");

    const saves = mockYamlSidecar();
    const card = screen.getByTestId(`feed-item-${item.id}`);
    fireEvent.click(within(card).getByRole("button", { name: "沉淀为关键词" }));
    const panel = await screen.findByTestId(`feed-keyword-pin-${item.id}`);
    fireEvent.change(within(panel).getByLabelText("沉淀关键词"), { target: { value: "LLM" } });
    fireEvent.click(within(panel).getByRole("button", { name: "写入" }));

    const note = await screen.findByTestId(`feed-keyword-note-${item.id}`);
    expect(note.textContent).toContain("已在目标 YAML");
    await waitFor(() =>
      expect(invokeMock).toHaveBeenCalledWith("sidecar_request", { method: "yaml.read", params: expect.anything() }),
    );
    expect(saves).toHaveLength(0);
  });

  it("G12 mtime_conflict:save 乐观锁拒绝 → 结构化码原样明示", async () => {
    const item = fixtureItem();
    storeItemsMock.mockResolvedValue(result([item]));
    renderScreen();
    await screen.findByText("条目 1");

    mockYamlSidecar({
      saveImpl: () =>
        Promise.reject(JSON.stringify({ code: "mtime_conflict", path: "params.file", message: "文件已被其他进程修改" })),
    });
    const card = screen.getByTestId(`feed-item-${item.id}`);
    fireEvent.click(within(card).getByRole("button", { name: "沉淀为关键词" }));
    const panel = await screen.findByTestId(`feed-keyword-pin-${item.id}`);
    fireEvent.click(within(panel).getByRole("button", { name: "写入" }));

    const note = await screen.findByTestId(`feed-keyword-note-${item.id}`);
    expect(note.textContent).toContain("mtime_conflict");
    expect(note.textContent).toContain("文件已被其他进程修改");
  });

  it("G12 清单拉取失败:list_error 明示 + 重试入口", async () => {
    const item = fixtureItem();
    storeItemsMock.mockResolvedValue(result([item]));
    renderScreen();
    await screen.findByText("条目 1");

    invokeMock.mockImplementation(async () => {
      throw JSON.stringify({ code: "source_dir_unreadable", path: "$", message: "插件目录不可读" });
    });
    const card = screen.getByTestId(`feed-item-${item.id}`);
    fireEvent.click(within(card).getByRole("button", { name: "沉淀为关键词" }));

    const error = await screen.findByTestId(`feed-keyword-list-error-${item.id}`);
    expect(error.textContent).toContain("source_dir_unreadable");
    expect(within(error).getByRole("button", { name: "重试" })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// D4 纯函数(api.ts):groupFeedItems 时间分桶边界 / categoryColor 稳定性
// ---------------------------------------------------------------------------

describe("feed D4 纯函数(api.ts)", () => {
  it("groupFeedItems:四桶按 今天/昨天/7天内/更早 边界落位,空桶不出组", () => {
    const now = new Date(2026, 9, 3, 12, 0); // 2026-10-03 正午(本地时区)
    const at = (iso: string) => fixtureItem({ first_seen: iso });
    const items = [
      at("2026-10-03T10:00:00"), // 今天
      at("2026-10-03T00:00:00"), // 今天(0 点边界含)
      at("2026-10-02T23:59:00"), // 昨天
      at("2026-09-30T10:00:00"), // 7 天内
      at("2026-09-26T00:00:00"), // 7 天内(7 天窗下边界含)
      at("2026-09-25T23:59:00"), // 更早
      at("不是时间"), // 更早(first_seen 无效归更早)
    ];
    const groups = groupFeedItems(items, now);
    expect(groups.map((group) => group.label)).toEqual(["今天", "昨天", "7 天内", "更早"]);
    expect(groups[0].items).toHaveLength(2);
    expect(groups[1].items).toHaveLength(1);
    expect(groups[2].items).toHaveLength(2);
    expect(groups[3].items).toHaveLength(2);
  });

  it("groupFeedItems:空桶过滤 + 桶内顺序保持(新→旧原序)", () => {
    const now = new Date(2026, 9, 3, 12, 0);
    const a = fixtureItem({ first_seen: "2026-10-03T09:00:00" });
    const b = fixtureItem({ first_seen: "2026-10-03T08:00:00" });
    const c = fixtureItem({ first_seen: "2026-09-01T08:00:00" });
    const groups = groupFeedItems([a, b, c], now);
    expect(groups.map((group) => group.key)).toEqual(["today", "earlier"]);
    expect(groups[0].items).toEqual([a, b]);
    expect(groups[1].items).toEqual([c]);
  });

  it("categoryColor:同品类恒同色(6 位 hex 色板内),空品类 null", () => {
    expect(categoryColor(null)).toBeNull();
    expect(categoryColor(undefined)).toBeNull();
    for (const category of ["ai-news", "stocks", "news", "wool", "monitor", "tech"]) {
      const color = categoryColor(category);
      expect(color).toMatch(/^#[0-9a-f]{6}$/);
      expect(categoryColor(category)).toBe(color); // 稳定散列:两次调用同色
    }
  });
});

// ---------------------------------------------------------------------------
// fe-small-batch 纯函数(api.ts):appendWatchlistKeyword 文本手术四形态 /
// 查重零写入 / id 行不动(跨文件 id 查重不破);setMarkerBulk 批量置位
// ---------------------------------------------------------------------------

describe("feed fe-small-batch 纯函数(api.ts)", () => {
  it("appendWatchlistKeyword 流式列表:闭括号前追加,尾随注释与其余行逐字节不动(id 行原样)", () => {
    const content = "# 顶注\nid: ai-news\nname: AI资讯\nwatchlist:\n  keywords: [LLM, Agent]  # 关注词\n  mute: [广告]\n";
    const outcome = appendWatchlistKeyword(content, "多模态");
    expect(outcome).toEqual({
      ok: true,
      content: "# 顶注\nid: ai-news\nname: AI资讯\nwatchlist:\n  keywords: [LLM, Agent, 多模态]  # 关注词\n  mute: [广告]\n",
    });
  });

  it("appendWatchlistKeyword 流式空表 []:首词入表;词已在(流式)→ already_present", () => {
    expect(appendWatchlistKeyword("watchlist:\n  keywords: []\n", "首词")).toEqual({
      ok: true,
      content: "watchlist:\n  keywords: [首词]\n",
    });
    expect(appendWatchlistKeyword("watchlist:\n  keywords: [LLM, Agent]\n", "LLM")).toEqual({
      ok: false,
      reason: "already_present",
    });
  });

  it("appendWatchlistKeyword 块式列表:末项后追加同缩进行,后续节点(mute)不动;词已在(块式)→ already_present", () => {
    const content = "id: ai-news\nwatchlist:\n  keywords:\n    - LLM\n    - Agent\n  mute:\n    - 广告\n";
    expect(appendWatchlistKeyword(content, "新词")).toEqual({
      ok: true,
      content: "id: ai-news\nwatchlist:\n  keywords:\n    - LLM\n    - Agent\n    - 新词\n  mute:\n    - 广告\n",
    });
    expect(appendWatchlistKeyword(content, "Agent")).toEqual({ ok: false, reason: "already_present" });
  });

  it("appendWatchlistKeyword 空值键行:keywords: 直接起首项;null 值先落定键行再起项", () => {
    expect(appendWatchlistKeyword("watchlist:\n  keywords:\n  mute: []\n", "首词")).toEqual({
      ok: true,
      content: "watchlist:\n  keywords:\n    - 首词\n  mute: []\n",
    });
    expect(appendWatchlistKeyword("watchlist:\n  keywords: null\n", "落定词")).toEqual({
      ok: true,
      content: "watchlist:\n  keywords:\n    - 落定词\n",
    });
  });

  it("appendWatchlistKeyword 无 keywords 键:有 watchlist 键补块;连 watchlist 都没有则末尾整节追加", () => {
    expect(appendWatchlistKeyword("id: x\nwatchlist:\n  mute: [广告]\n", "补词")).toEqual({
      ok: true,
      content: "id: x\nwatchlist:\n  keywords:\n    - 补词\n  mute: [广告]\n",
    });
    expect(appendWatchlistKeyword("id: x\n", "末词")).toEqual({
      ok: true,
      content: "id: x\nwatchlist:\n  keywords:\n    - 末词\n",
    });
    expect(appendWatchlistKeyword("", "孤词")).toEqual({
      ok: true,
      content: "watchlist:\n  keywords:\n    - 孤词\n",
    });
  });

  it("appendWatchlistKeyword 边界:标量值 keywords_unparsed / 空词 / 超长(>64);特殊字符词双引号转义", () => {
    expect(appendWatchlistKeyword("watchlist:\n  keywords: LLM\n", "新词")).toEqual({
      ok: false,
      reason: "keywords_unparsed",
    });
    expect(appendWatchlistKeyword("watchlist:\n  keywords: []\n", "  ")).toEqual({ ok: false, reason: "empty_keyword" });
    expect(appendWatchlistKeyword("watchlist:\n  keywords: []\n", "词".repeat(65))).toEqual({
      ok: false,
      reason: "keyword_too_long",
    });
    const quoted = appendWatchlistKeyword("watchlist:\n  keywords: []\n", "a,b:c");
    expect(quoted).toEqual({ ok: true, content: "watchlist:\n  keywords: [\"a,b:c\"]\n" });
  });

  it("setMarkerBulk:全部置 read=true 逐条落键;置回 false 时无其他标记的键剪除", () => {
    const items = [fixtureItem(), fixtureItem()];
    const states = { [items[1].dedup_key]: { starred: true } };
    const allRead = setMarkerBulk(items, states, "read", true);
    expect(allRead[items[0].dedup_key]).toEqual({ read: true });
    expect(allRead[items[1].dedup_key]).toEqual({ starred: true, read: true });
    const backUnread = setMarkerBulk(items, allRead, "read", false);
    expect(backUnread[items[0].dedup_key]).toBeUndefined();
    // 有其他标记的键保留显式 read:false(与 toggleMarker 的存储纪律一致:
    // 显式 false 与缺省同义,只有三态全 false 才剪键)
    expect(backUnread[items[1].dedup_key]).toEqual({ starred: true, read: false });
  });
});
