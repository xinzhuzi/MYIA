// @vitest-environment jsdom
//
// 源管理组件测试:mock sidecar(壳命令 sidecar_request 的 JS 假实现,
// 内存态模拟品类 YAML 的 sources 名单),覆盖:表格渲染与健康度三色 /
// 排序筛选分页 / 启停写回+doctor 往返复核 / 写回失败结构化错误态 / 空态 / 加载错误态 /
// 行动作「编辑」到配置编辑屏的链接。
// 协议缺口(method_not_found)也是被测行为之一 —— sources.write 未收编前如实呈现。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

const mocks = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));

import { SourcesScreen } from "./sources-screen";
import type { DoctorResult, DoctorPluginReport, HealthResult, PluginReport, SourceReport, SourceHealthState } from "@/lib/api";

const FILE = "plugins/ai-news.yaml";

// ---------------------------------------------------------------------------
// 协议夹具(形状逐字段对照 src/lib/api/types.ts / desktop/entry.py)
// ---------------------------------------------------------------------------

function sourceReport(name: string, state: SourceHealthState, url = `https://example.com/${name}`): SourceReport {
  return {
    name,
    url,
    engine: "static_html",
    engine_hint: null,
    health: {
      state,
      reason: `评判原因:${state}`,
      observed: 3,
      latest: { run_id: 7, run_status: "success", item_count: 2, skip_reason: null, failed: false },
      baseline: 2,
    },
    fingerprint_skips: { observed: 5, skipped: 1 },
  };
}

function pluginReport(file: string, id: string, sources: SourceReport[]): PluginReport {
  return {
    file,
    id,
    name: `品类 ${id}`,
    schedule: "*/15 * * * *",
    timezone: "Asia/Shanghai",
    push_channels: ["feishu_card"],
    loaded: true,
    load_errors: null,
    sources,
  };
}

function healthResult(plugins: PluginReport[]): HealthResult {
  const counts = { ok: 0, degraded: 0, dead: 0, unknown: 0 };
  let sources = 0;
  for (const plugin of plugins) {
    sources += plugin.sources.length;
    for (const source of plugin.sources) counts[source.health.state] += 1;
  }
  return {
    command: "list",
    plugins_dir: "plugins",
    db: "myia.db",
    store_error: null,
    plugins,
    summary: { plugins: plugins.length, sources, ...counts },
    healthy: counts.dead === 0,
    exit_code: 0,
  };
}

function doctorResult(file: string, sourceNames: string[]): DoctorResult {
  const plugin: DoctorPluginReport = {
    ...pluginReport(file, "ai-news", sourceNames.map((name) => sourceReport(name, "ok"))),
    next_fire_at: null,
    enrich: null,
  };
  return {
    command: "doctor",
    generated_at: "2026-10-02T10:00:00.000Z",
    db: "myia.db",
    healthy: true,
    plugins: [plugin],
    credentials: { backend_available: true, backend_error: null, entries: [] },
    proxy: { config: null, pools: [] },
    findings: [],
    summary: { plugins: 1, sources: sourceNames.length, errors: 0, warnings: 0 },
  };
}

// ---------------------------------------------------------------------------
// mock sidecar:内存态 = 品类 YAML 的 sources 名单 + 停用暂存
// ---------------------------------------------------------------------------

type Handler = (params: never) => unknown;
type SidecarMap = Record<string, Handler>;

function okSidecar(initialSources: string[]) {
  const state = {
    enabled: [...initialSources],
    disabled: new Set<string>(),
  };
  const map: SidecarMap = {
    health: () =>
      healthResult([pluginReport(FILE, "ai-news", state.enabled.map((name) => sourceReport(name, "ok")))]),
    "sources.write": (params: never) => {
      const { file, enable = [], disable = [] } = params as {
        file: string;
        enable?: string[];
        disable?: string[];
      };
      for (const name of disable) {
        state.enabled = state.enabled.filter((candidate) => candidate !== name);
        state.disabled.add(name);
      }
      for (const name of enable) {
        if (!state.enabled.includes(name)) state.enabled.push(name);
        state.disabled.delete(name);
      }
      return { file, written: true, enabled: [...state.enabled], disabled: [...state.disabled] };
    },
    doctor: (params: never) => {
      const { yamls } = params as { yamls?: string[] };
      const file = yamls?.[0] ?? FILE;
      return doctorResult(file, state.enabled);
    },
  };
  return { map, state };
}

/** 安装 mock sidecar:所有 invoke("sidecar_request") 走此分派;未知方法=结构化 404 */
function installSidecar(map: SidecarMap): void {
  mocks.invoke.mockImplementation(
    async (_command: string, args: { method: string; params?: unknown }) => {
      const handler = map[args.method];
      if (!handler) {
        throw JSON.stringify({
          code: "method_not_found",
          path: "method",
          message: `未知方法 ${args.method}`,
          data: { allowed: Object.keys(map).sort() },
        });
      }
      return handler(args.params as never);
    },
  );
}

function lastCall(method: string): { params: unknown } | undefined {
  const calls = mocks.invoke.mock.calls.filter(([, args]) => (args as { method: string }).method === method);
  return calls.at(-1)?.[1] as { params: unknown } | undefined;
}

beforeEach(() => {
  mocks.invoke.mockReset();
});
afterEach(() => {
  cleanup(); // vitest 非 globals 模式下 RTL 不自动清理,防 DOM 跨测试污染
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------

describe("源管理:表格渲染与健康度三色", () => {
  it("health 数据扁平成行:源名/URL/引擎/健康度徽标齐备", async () => {
    const { map } = okSidecar(["hacker-news", "rsshub"]);
    map.health = () =>
      healthResult([
        pluginReport(FILE, "ai-news", [
          sourceReport("hacker-news", "ok"),
          sourceReport("rsshub", "degraded"),
          sourceReport("slow-site", "dead"),
        ]),
      ]);
    installSidecar(map);

    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );

    expect(await screen.findByText("hacker-news")).toBeTruthy();
    expect(screen.getByText("https://example.com/rsshub")).toBeTruthy();
    expect(screen.getAllByText("static_html").length).toBeGreaterThan(0);
    // 三色徽标:正常(绿)/退化(琥珀)/失效(红);data-health 钉住语义,
    // 与筛选 chips(同名文案按钮)区分
    expect(document.querySelector("[data-health='ok']")?.textContent).toBe("正常");
    expect(document.querySelector("[data-health='degraded']")?.textContent).toBe("退化");
    expect(document.querySelector("[data-health='dead']")?.textContent).toBe("失效");
  });
});

describe("源管理:排序/筛选/分页", () => {
  function manyRowsSidecar() {
    const { map, state } = okSidecar([]);
    const names = Array.from({ length: 12 }, (_, index) => `source-${String(index).padStart(2, "0")}`);
    map.health = () =>
      healthResult([pluginReport(FILE, "ai-news", names.map((name) => sourceReport(name, "ok")))]);
    state.enabled = names;
    return { map, state, names };
  }

  it("12 行分两页;下一页/上一页翻动;筛选即时收窄", async () => {
    installSidecar(manyRowsSidecar().map);
    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("source-00")).toBeTruthy();
    expect(screen.getByText(/共 12 行/)).toBeTruthy();
    expect(screen.getByText("第 1 / 2 页")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByText("第 2 / 2 页")).toBeTruthy();
    expect(screen.queryByText("source-00")).toBeNull();
    expect(screen.getByText("source-11")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "上一页" }));
    const filter = screen.getByLabelText("全局筛选") as HTMLInputElement;
    fireEvent.change(filter, { target: { value: "source-1" } });
    await waitFor(() => {
      expect(screen.getByText(/共 2 行/)).toBeTruthy(); // 00..11 中含 "source-1" 的只有 10/11
    });
    expect(screen.queryByText(/^source-0/)).toBeNull();
  });

  it("点列头排序(URL 升序),健康度 chips 过滤", async () => {
    const { map } = okSidecar(["beta", "alpha"]);
    map.health = () =>
      healthResult([
        pluginReport(FILE, "ai-news", [
          sourceReport("beta", "ok", "https://example.com/beta"),
          sourceReport("alpha", "dead", "https://example.com/alpha"),
        ]),
      ]);
    installSidecar(map);
    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("beta")).toBeTruthy();

    const urlHeader = screen.getByRole("columnheader", { name: /URL/ });
    fireEvent.click(urlHeader);
    expect(urlHeader.getAttribute("aria-sort")).toBe("ascending");
    const firstRowUrl = screen.getAllByText(/https:\/\/example\.com\//)[0];
    expect(firstRowUrl.textContent).toContain("alpha");

    fireEvent.click(screen.getByRole("button", { name: "失效" }));
    await waitFor(() => {
      expect(screen.getByText(/共 1 行/)).toBeTruthy();
    });
    expect(screen.queryByText("beta")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "全部" }));
    await waitFor(() => {
      expect(screen.getByText("beta")).toBeTruthy();
    });
  });
});

describe("源管理:启停写回 + doctor 往返复核", () => {
  it("停用:写回 sources.write(disable) → doctor(yamls) 复核一致 → 行随刷新消失并进停用区", async () => {
    const { map, state } = okSidecar(["hacker-news", "rsshub"]);
    installSidecar(map);
    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("hacker-news")).toBeTruthy();

    fireEvent.click(screen.getByRole("switch", { name: "停用 hacker-news" }));

    expect(await screen.findByTestId("roundtrip-ok")).toBeTruthy();
    expect(screen.getByTestId("roundtrip-ok").textContent).toContain("已停用 hacker-news");
    expect(screen.getByTestId("roundtrip-ok").textContent).toContain("doctor 复核往返一致");

    // 写回参数:品类文件 + disable 名单;复核用 doctor(yamls:[file])
    expect(lastCall("sources.write")?.params).toEqual({ file: FILE, disable: ["hacker-news"] });
    expect(lastCall("doctor")?.params).toEqual({ yamls: [FILE] });
    // mock 内存态(YAML 代理)真的变了;刷新后表格行消失(URL 唯一,停用区不含 URL)、进「已停用」区
    expect(state.enabled).toEqual(["rsshub"]);
    await waitFor(() => {
      expect(screen.queryByText("https://example.com/hacker-news")).toBeNull();
    });
    expect(screen.getByText("本次会话已停用(1)")).toBeTruthy();
  });

  it("再启用:停用区按钮写回 enable → doctor 复核一致 → 行回到表格", async () => {
    const { map } = okSidecar(["hacker-news"]);
    installSidecar(map);
    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("hacker-news")).toBeTruthy();
    fireEvent.click(screen.getByRole("switch", { name: "停用 hacker-news" }));
    await screen.findByTestId("roundtrip-ok");

    fireEvent.click(screen.getByRole("button", { name: /^启用$/ }));
    const okBanner = await screen.findByTestId("roundtrip-ok");
    expect(okBanner.textContent).toContain("已启用 hacker-news");
    expect(lastCall("sources.write")?.params).toEqual({ file: FILE, enable: ["hacker-news"] });
    await waitFor(() => {
      expect(screen.getByText("hacker-news")).toBeTruthy();
    });
  });

  it("协议缺口:sidecar 无 sources.write → 结构化 method_not_found 错误态,开关不动", async () => {
    const { map } = okSidecar(["hacker-news"]);
    const withoutWrite: SidecarMap = { health: map.health, doctor: map.doctor };
    installSidecar(withoutWrite);
    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("hacker-news")).toBeTruthy();

    fireEvent.click(screen.getByRole("switch", { name: "停用 hacker-news" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("未知方法 sources.write");
    expect(alert.textContent).toContain("code=method_not_found");
    expect(alert.textContent).toContain("sidecar 协议尚无此方法");
    // 失败即回滚呈现:开关仍可点(未写入),无「往返一致」假象
    expect(screen.queryByTestId("roundtrip-ok")).toBeNull();
    expect(screen.getByRole("switch", { name: "停用 hacker-news" })).toBeTruthy();
  });
});

describe("源管理:空态与错误态", () => {
  it("无插件 → 品牌空态", async () => {
    const { map } = okSidecar([]);
    map.health = () => healthResult([]);
    installSidecar(map);
    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("还没有源数据")).toBeTruthy();
    expect(screen.getByText(/插件目录\(plugins\)下没有可加载的品类 YAML/)).toBeTruthy();
  });

  it("sidecar 不可用 → 结构化错误(code/path)+ 重试", async () => {
    mocks.invoke.mockImplementation(async () => {
      throw JSON.stringify({ code: "sidecar_not_running", path: "$", message: "sidecar 进程未运行" });
    });
    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("sidecar 进程未运行");
    expect(alert.textContent).toContain("code=sidecar_not_running");
    expect(alert.textContent).toContain("path=$");
    // 重试走同一 mock(仍失败,但按钮行为可达)
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => {
      expect(mocks.invoke.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("行动作「编辑」:链接到 /yaml-editor?file=…(预选该品类文件)", async () => {
    const { map } = okSidecar(["hacker-news"]);
    installSidecar(map);
    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("hacker-news")).toBeTruthy();

    const links = screen.getAllByRole("link", { name: "编辑" });
    expect(links.length).toBe(1); // 单源品类一行一链接
    expect(links[0].getAttribute("href")).toBe(`/yaml-editor?file=${encodeURIComponent(FILE)}`);
  });
});
