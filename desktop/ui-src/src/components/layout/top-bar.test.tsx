// @vitest-environment jsdom
//
// 顶栏接线测试:①品类选择器(C8,10-03-feed-ux)选项 = health().plugins 的
// id 去重 + 名称回显;选中回调上抛(null = 全部品类);health 失败静默收敛。
// ②D4 壳层:面包屑(世事 › 分组 › 页面,与侧栏导航同口径)+ 全局命令位留白。
// TopBar 现消费 useLocation(面包屑),须在 MemoryRouter 下渲染。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

const mocks = vi.hoisted(() => ({ invoke: vi.fn(), listen: vi.fn(), health: vi.fn(), version: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, health: mocks.health, version: mocks.version } };
});

import { TopBar } from "@/components/layout/top-bar";
import type { HealthResult } from "@/lib/api";

function healthOf(pluginIds: { id: string | null; name: string | null }[]): HealthResult {
  return {
    command: "list",
    plugins_dir: "/home/plugins",
    db: "/home/myia.db",
    store_error: null,
    plugins: pluginIds.map((entry, index) => ({
      file: `/home/plugins/${entry.id ?? `broken-${index}`}.yaml`,
      id: entry.id,
      name: entry.name,
      schedule: null,
      timezone: null,
      push_channels: [],
      loaded: true,
      load_errors: null,
      sources: [],
    })),
    summary: { plugins: pluginIds.length, sources: 0, ok: 0, degraded: 0, dead: 0, unknown: 0 },
    healthy: true,
    first_run: false,
    exit_code: 0,
  } as HealthResult;
}

beforeEach(() => {
  mocks.version.mockResolvedValue({ name: "shishi", version: "1.1.1", protocol: 3 });
  mocks.listen.mockResolvedValue(() => undefined);
  mocks.invoke.mockResolvedValue({ restarted: true });
  // Radix Select 高亮滚动 jsdom 未实现,补 stub(真实浏览器原生)
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderTopBar(
  onCategoryChange: (next: string | null) => void = vi.fn(),
  category: string | null = null,
  initialEntry = "/",
) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <TopBar category={category} onCategoryChange={onCategoryChange} />
    </MemoryRouter>,
  );
}

describe("TopBar 品类选择器(C8)", () => {
  it("选项 = health().plugins 的 id 去重(id=null 不入),名称回显;选中上抛回调", async () => {
    const onChange = vi.fn();
    mocks.health.mockResolvedValue(
      healthOf([
        { id: "ai-news", name: "AI资讯" },
        { id: "stocks", name: "股票" },
        { id: "ai-news", name: "重复 id" }, // 同 id 多文件:首现优先
        { id: null, name: "装不上的" }, // id=null 不入选项
      ]),
    );
    renderTopBar(onChange);

    // Radix Select 受控组件:展开后点选「AI资讯」→ 上抛 id
    fireEvent.click(screen.getByLabelText("品类选择"));
    const option = await screen.findByRole("option", { name: "AI资讯" });
    expect(option).toBeTruthy();
    fireEvent.click(option);
    expect(onChange).toHaveBeenCalledWith("ai-news");
  });

  it("「全部品类」选中 = 上抛 null(协议不传参);受控值回显", async () => {
    const onChange = vi.fn();
    mocks.health.mockResolvedValue(healthOf([{ id: "stocks", name: "股票" }]));
    // 从已选品类出发( Radix 对重选当前值不派发变更,测的是切换路径)
    renderTopBar(onChange, "stocks");

    fireEvent.click(screen.getByLabelText("品类选择"));
    fireEvent.click(await screen.findByRole("option", { name: "全部品类" }));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("health 失败静默:不阻塞顶栏,选项收敛为仅「全部品类」", async () => {
    mocks.health.mockRejectedValue(new Error("sidecar 未连接"));
    renderTopBar();

    await waitFor(() => expect(mocks.health).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByLabelText("品类选择"));
    const options = await screen.findAllByRole("option");
    expect(options.map((node) => node.textContent)).toEqual(["全部品类"]);
  });
});

describe("TopBar 壳层件(D4:面包屑+全局命令位)", () => {
  it("面包屑 = 世事 › 分组 › 页面(与侧栏导航同一解析口径)", () => {
    renderTopBar(undefined, null, "/sources");
    const nav = screen.getByRole("navigation", { name: "面包屑" });
    expect(within(nav).getByRole("link", { name: "世事" })).toBeTruthy();
    expect(within(nav).getByText("采集")).toBeTruthy(); // 分组段(弱色)
    expect(within(nav).getByText("源管理")).toBeTruthy(); // 页面段(末段)
  });

  it("主入口区路由无分组段:世事 › 仪表盘(根路由)", () => {
    renderTopBar(undefined, null, "/");
    const nav = screen.getByRole("navigation", { name: "面包屑" });
    expect(within(nav).getByRole("link", { name: "世事" })).toBeTruthy();
    expect(within(nav).getByText("仪表盘")).toBeTruthy();
    // 无分组段:任何组标题都不出现
    for (const group of ["采集", "推送"]) {
      expect(within(nav).queryByText(group)).toBeNull();
    }
  });

  it("底部设置路由也在面包屑口径内(世事 › 设置)", () => {
    renderTopBar(undefined, null, "/settings");
    const nav = screen.getByRole("navigation", { name: "面包屑" });
    expect(within(nav).getByRole("link", { name: "世事" })).toBeTruthy();
    expect(within(nav).getByText("设置")).toBeTruthy();
    expect(within(nav).queryByText("采集")).toBeNull();
  });

  it("全局命令位留白:预留位存在(占位文案+快捷键提示),非交互", () => {
    renderTopBar();
    const slot = screen.getByTitle("全局命令位:命令面板属后续版本,此处仅预留位置");
    expect(slot.getAttribute("aria-hidden")).toBe("true");
    expect(slot.textContent).toContain("搜索或跳转…");
    // 快捷键文案只做视觉锚点(mac ⌘K / 其余 Ctrl K,按运行平台)
    expect(/⌘K|Ctrl K/.test(slot.textContent ?? "")).toBe(true);
  });
});
