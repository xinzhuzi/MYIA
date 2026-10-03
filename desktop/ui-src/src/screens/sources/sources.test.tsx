// @vitest-environment jsdom
//
// 源管理组件测试:mock sidecar(壳命令 sidecar_request 的 JS 假实现,
// 内存态模拟品类 YAML 的 sources 名单),覆盖:表格渲染与健康度三色 /
// 排序筛选分页 / 启停写回+doctor 往返复核 / 写回失败结构化错误态 / 空态 / 加载错误态 /
// 行动作「编辑」当场弹出 YAML 编辑对话框并加载该文件原文(不离开本屏)。
// 协议缺口(method_not_found)也是被测行为之一 —— sources.write 未收编前如实呈现。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

const mocks = vi.hoisted(() => ({ invoke: vi.fn(), listen: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
// C13(10-03-v112-desktop-parity):test.completed 事件经 onSidecarEvent(listen)
// 回屏 —— 捕获 handler 供用例回放事件载荷
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));
// 编辑器替身:受控 textarea(弹窗内 CodeMirror 以同款契约替身;真实渲染见
// yaml-editor/editor-pane.test.tsx)
vi.mock("@uiw/react-codemirror", () => ({
  default: (props: { value?: string; onChange?: (value: string) => void }) => (
    <textarea
      aria-label="yaml-source"
      value={props.value ?? ""}
      onChange={(event) => props.onChange?.(event.target.value)}
    />
  ),
}));

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
    "yaml.read": (params: never) => {
      const { file } = params as { file: string };
      return {
        file,
        content: `# 原文注释:逐字节保真\nid: ai-news\nname: AI 新闻\nschedule: "*/15 * * * *"\n`,
        size: 64,
        mtime: 111.5,
      };
    },
    "yaml.save": (params: never) => {
      const { file } = params as { file: string };
      return { file, written: true, created: false, backed_up: `${file}.bak`, mtime: 222.25, warnings: [] };
    },
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

function callCount(method: string): number {
  return mocks.invoke.mock.calls.filter(([, args]) => (args as { method: string }).method === method).length;
}

beforeEach(() => {
  mocks.invoke.mockReset();
  mocks.listen.mockReset();
  // 缺省:订阅即成功、零事件(C13 用例再覆盖捕获 handler);绝不能返回
  // undefined —— 屏内 onSidecarEvent(...).then 会炸
  mocks.listen.mockImplementation(async () => () => undefined);
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

  it("行动作「编辑」:当场弹出编辑对话框并加载该品类文件原文(不离开源管理屏)", async () => {
    const { map } = okSidecar(["hacker-news"]);
    installSidecar(map);
    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("hacker-news")).toBeTruthy();

    // 编辑改为弹窗:不再是跳转链接
    expect(screen.queryByRole("link", { name: "编辑" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));

    // 模态对话框出现(遮罩 + role=dialog aria-modal),编辑器加载该文件原文
    const dialog = await screen.findByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label")).toContain("ai-news.yaml");
    await waitFor(() => {
      expect((screen.getByLabelText("yaml-source") as HTMLTextAreaElement).value).toContain("id: ai-news");
    });
    expect(lastCall("yaml.read")?.params).toEqual({ file: FILE });

    // 弹窗内保存成功 → 表格数据刷新(health 二次拉取,防编辑后展示陈旧行)
    fireEvent.change(screen.getByLabelText("yaml-source"), {
      target: { value: "# 改稿\nid: ai-news\nname: AI 新闻\nschedule: \"0 9 * * *\"\n" },
    });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));
    expect(await screen.findByTestId("save-ok")).toBeTruthy();
    await waitFor(() => {
      expect(callCount("health")).toBe(2);
    });

    // 保存后干净态关闭:ESC 直接关
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });
});

// ---------------------------------------------------------------------------
// C13(10-03-v112-desktop-parity):行「试抓」(sources.test 异步 job → test.completed 回显)
// ---------------------------------------------------------------------------

describe("源管理:试抓此源(C13)", () => {
  type EventHanlder = (event: { payload: Record<string, unknown> }) => void;

  function installEvents() {
    let handler: EventHanlder | null = null;
    mocks.listen.mockImplementation(async (_name: string, fn: EventHanlder) => {
      handler = fn;
      return () => undefined;
    });
    return {
      emit: (payload: Record<string, unknown>) => handler?.({ payload }),
    };
  }

  it("行按钮发起 sources.test(job_id 对账)→ 事件回显引擎/条数/指纹摘要", async () => {
    const events = installEvents();
    const { map } = okSidecar(["local-api"]);
    map["sources.test"] = (params: never) => {
      const { source } = params as { file: string; source: string };
      return { job_id: 7, state: "running", source };
    };
    installSidecar(map);
    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );

    await screen.findByText("local-api");
    fireEvent.click(screen.getByRole("button", { name: "试抓 local-api" }));
    await waitFor(() => expect(lastCall("sources.test")?.params).toEqual({ file: FILE, source: "local-api" }));
    expect(await screen.findByTestId("test-running")).toBeTruthy();

    events.emit({
      type: "test.completed",
      job_id: 7,
      ok: true,
      exit_code: 0,
      result: {
        command: "test",
        sources: [
          {
            source: "local-api",
            engine: "direct_api",
            engine_configured: "auto",
            ok: true,
            item_count: 2,
            failures: [],
            fingerprint: { verdict: "changed_or_first_fetch", meaning: "内容有变化或首次抓取,线上调度会正常提取" },
          },
        ],
      },
      ts: "2026-10-03T08:00:00+00:00",
    });

    const banner = await screen.findByTestId("test-result-ok");
    expect(banner.textContent).toContain("local-api");
    expect(banner.textContent).toContain("direct_api");
    expect(banner.textContent).toContain("2 条");
    expect(banner.textContent).toContain("内容有变化或首次抓取");
    expect(screen.queryByTestId("test-running")).toBeNull(); // job 收尾
  });

  it("失败形:CLI config 错经事件透传(ok=false)→ 红条回显;发起被拒也如实上屏", async () => {
    const events = installEvents();
    const { map } = okSidecar(["local-api"]);
    map["sources.test"] = () => ({ job_id: 9, state: "running", source: "local-api" });
    installSidecar(map);
    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );

    await screen.findByText("local-api");
    fireEvent.click(screen.getByRole("button", { name: "试抓 local-api" }));
    await screen.findByTestId("test-running");
    events.emit({
      type: "test.completed",
      job_id: 9,
      ok: false,
      exit_code: 1,
      error: "config",
      data: { error: "config", errors: [{ path: "$.sources", message: "源 'x' 不存在" }] },
      ts: "2026-10-03T08:00:00+00:00",
    });

    const banner = await screen.findByTestId("test-result-fail");
    expect(banner.textContent).toContain("config");
    expect(banner.textContent).toContain("源 'x' 不存在");

    // 发起失败(test_busy 单飞)→ 同一红条位呈现,不静默
    map["sources.test"] = () => {
      throw JSON.stringify({
        code: "test_busy",
        path: "$",
        message: "已有试抓在执行 job_id=9(单飞)",
        data: { active_job_id: 9 },
      });
    };
    fireEvent.click(screen.getByRole("button", { name: "试抓 local-api" }));
    const busy = await screen.findByTestId("test-result-fail");
    expect(busy.textContent).toContain("test_busy");
  });
});

// ---------------------------------------------------------------------------
// 排程一览(G4,10-03-feed-ux):schedule.preview 逐品类并发,单品类失败不塌整区
// ---------------------------------------------------------------------------

describe("源管理:排程一览(G4)", () => {
  it("逐品类出 schedule/timezone 原文 + 未来时刻行;无排程品类明示「无排程」", async () => {
    const { map } = okSidecar(["local-api"]);
    map["schedule.preview"] = (params: never) => {
      const { file } = params as { file: string };
      if (file.endsWith("nosched.yaml")) {
        return { file, schedule: null, timezone: null, runs: [] };
      }
      return {
        file,
        schedule: "*/15 * * * *",
        timezone: "Asia/Shanghai",
        runs: ["2026-10-03T09:00:00+08:00", "2026-10-03T09:15:00+08:00"],
      };
    };
    map.health = () =>
      healthResult([
        pluginReport(FILE, "ai-news", [sourceReport("local-api", "ok")]),
        pluginReport("plugins/nosched.yaml", "nosched", []),
      ]);
    installSidecar(map);

    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );

    const overview = await screen.findByTestId("schedule-overview");
    expect(overview.textContent).toContain("排程一览");
    // schedule 原文 + 时区 + 本地化时刻
    const row = screen.getByTestId(`schedule-row-${FILE}`);
    expect(row.textContent).toContain("*/15 * * * *");
    expect(row.textContent).toContain("Asia/Shanghai");
    expect(row.textContent).toContain("09:00");
    // 无排程品类明示,不是错误
    const nosched = screen.getByTestId("schedule-row-plugins/nosched.yaml");
    expect(nosched.textContent).toContain("无排程");
    // 预览请求逐品类发出,且带 count(缺省 5)
    const previewParams = mocks.invoke.mock.calls
      .filter(([, args]) => (args as { method: string }).method === "schedule.preview")
      .map(([, args]) => (args as { params: unknown }).params);
    expect(previewParams).toContainEqual({ file: FILE, count: 5 });
    expect(previewParams).toContainEqual({ file: "plugins/nosched.yaml", count: 5 });
  });

  it("单品类预览失败只塌该行(预览失败徽标 + code),整区仍出", async () => {
    const { map } = okSidecar(["local-api"]);
    map["schedule.preview"] = (params: never) => {
      const { file } = params as { file: string };
      if (file.endsWith("bad.yaml")) {
        throw JSON.stringify({
          code: "source_file_unreadable",
          path: "params.file",
          message: "品类 YAML 装不上: invalid_cron",
        });
      }
      return { file, schedule: "0 9 * * *", timezone: null, runs: ["2026-10-04T09:00:00+08:00"] };
    };
    map.health = () =>
      healthResult([
        pluginReport(FILE, "ai-news", [sourceReport("local-api", "ok")]),
        pluginReport("plugins/bad.yaml", "bad", []),
      ]);
    installSidecar(map);

    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );

    const badRow = await screen.findByTestId("schedule-row-plugins/bad.yaml");
    expect(badRow.textContent).toContain("预览失败");
    expect(badRow.textContent).toContain("source_file_unreadable");
    // 好品类照常出(allSettled 不塌整区)
    expect(screen.getByTestId(`schedule-row-${FILE}`).textContent).toContain("0 9 * * *");
  });

  it("空品类目录:排程一栏给空态引导文案", async () => {
    const { map } = okSidecar([]);
    map.health = () => healthResult([]);
    map["schedule.preview"] = () => ({ file: "", schedule: null, timezone: null, runs: [] });
    installSidecar(map);

    render(
      <MemoryRouter>
        <SourcesScreen />
      </MemoryRouter>,
    );

    const overview = await screen.findByTestId("schedule-overview");
    expect(overview.textContent).toContain("没有品类 YAML");
  });
});
