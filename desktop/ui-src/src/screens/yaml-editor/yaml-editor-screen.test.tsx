// @vitest-environment jsdom
//
// 配置编辑屏组件测试:mock sidecar(壳命令 sidecar_request 的 JS 假实现,
// 内存态模拟 plugins 目录)+ mock @uiw/react-codemirror(textarea 受控替身,
// 编辑器真实渲染的 jsdom 集成见 editor-pane.test.tsx)。覆盖(design §7 UI 清单):
// list 渲染+坏文件徽标 / ?file= 预选读原文+完整路径 / 读取失败错误态 /
// dirty 守卫(拦截切换 + SPA 路由离开) / 校验干跑 findings 分级 / 保存失败结构化错误 /
// 保存成功(+自动 doctor 复核+warnings 带回) / 新建流(stem 预检→模板草稿→
// save null mtime) / 跑一次(dirty 禁用→发起) / 采集运行中只提示不拦 /
// 删除(confirm 文案+列表刷新+选中回 idle) / Cmd+S 保存。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryRouter, Link, RouterProvider } from "react-router-dom";

const mocks = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
// 编辑器替身:受控 textarea(屏层逻辑只依赖 value/onChange 契约)
vi.mock("@uiw/react-codemirror", () => ({
  default: (props: { value?: string; onChange?: (value: string) => void }) => (
    <textarea
      aria-label="yaml-source"
      value={props.value ?? ""}
      onChange={(event) => props.onChange?.(event.target.value)}
    />
  ),
}));

import { YamlEditorScreen } from "./yaml-editor-screen";
import type { YamlFileEntry } from "./api";
import type { DoctorResult } from "@/lib/api";

const DIR = "/repo/plugins";
const AI = `${DIR}/ai-news.yaml`;
const BROKEN = `${DIR}/broken.yaml`;
const AI_CONTENT = `# 头注释:逐字节保真的原文\nid: ai-news\nname: AI 新闻\nschedule: "*/15 * * * *"\nsources:\n  - name: hacker-news\n    url: "https://example.com/news"\n`;
const BROKEN_CONTENT = "::: 不是合法 yaml";
const TEMPLATE = `# 最小品类模板\nid: my-category\nname: 我的品类\nschedule: "0 9 * * *"\nsources:\n  - name: example\n    url: "https://example.com/"\n`;

// ---------------------------------------------------------------------------
// 夹具(形状逐字段对照 ./api.ts 契约 / desktop/entry.py `_m_yaml_*`)
// ---------------------------------------------------------------------------

function goodEntry(): YamlFileEntry {
  return {
    file: AI,
    name: "ai-news.yaml",
    parse_ok: true,
    category_id: "ai-news",
    category_name: "AI 新闻",
    sources: 2,
    error: null,
  };
}

const brokenEntry: YamlFileEntry = {
  file: BROKEN,
  name: "broken.yaml",
  parse_ok: false,
  category_id: null,
  category_name: null,
  sources: null,
  error: { path: "$", code: "yaml_parse_error", message: "YAML 语法无法解析: mapping values are not allowed here" },
};

function doctorOk(file: string, name: string, sources: number): DoctorResult {
  return {
    command: "doctor",
    generated_at: "2026-10-03T10:00:00.000Z",
    db: "myia.db",
    healthy: true,
    plugins: [
      {
        file,
        id: "ai-news",
        name,
        schedule: "*/15 * * * *",
        timezone: "Asia/Shanghai",
        push_channels: [],
        loaded: true,
        load_errors: null,
        sources: Array.from({ length: sources }, (_, index) => ({
          name: `source-${index}`,
          url: "https://example.com/",
          engine: "static_html",
          engine_hint: null,
          health: {
            state: "ok",
            reason: "",
            observed: 0,
            latest: null,
            baseline: null,
          },
          fingerprint_skips: { observed: 0, skipped: 0 },
        })),
        next_fire_at: null,
        enrich: null,
      },
    ],
    credentials: { backend_available: true, backend_error: null, entries: [] },
    proxy: { config: null, pools: [] },
    findings: [],
    summary: { plugins: 1, sources, errors: 0, warnings: 0 },
  };
}

// ---------------------------------------------------------------------------
// mock sidecar:内存态 = plugins 目录的品类 YAML 清单与内容
// ---------------------------------------------------------------------------

type Handler = (params: never) => unknown;
type SidecarMap = Record<string, Handler>;

/** 结构化协议错误(与 Rust Err(String) 同形态:JSON 文本) */
function errJson(code: string, message: string, path = "$"): string {
  return JSON.stringify({ code, path, message });
}

function okSidecar() {
  const state = {
    files: [goodEntry(), brokenEntry] as YamlFileEntry[],
    contents: new Map<string, string>([
      [AI, AI_CONTENT],
      [BROKEN, BROKEN_CONTENT],
    ]),
    mtimes: new Map<string, number>([[AI, 111.5]]),
    saved: [] as unknown[],
    deleted: [] as string[],
    started: [] as unknown[],
  };
  const map: SidecarMap = {
    "yaml.list": () => ({
      plugins_dir: DIR,
      files: [...state.files].sort((a, b) => a.name.localeCompare(b.name)),
    }),
    "yaml.read": (params: never) => {
      const { file } = params as { file: string };
      if (!state.contents.has(file)) throw errJson("file_not_found", `文件不存在: ${file}`, "params.file");
      return {
        file,
        content: state.contents.get(file),
        size: 123,
        mtime: state.mtimes.get(file) ?? 0,
      };
    },
    "yaml.validate": () => ({
      valid: true,
      findings: [],
      category: { id: "ai-news", name: "AI 新闻", sources: 2 },
    }),
    "yaml.template": () => ({ content: TEMPLATE }),
    "yaml.save": (params: never) => {
      const { file, content } = params as { file: string; content: string };
      const created = !state.contents.has(file);
      state.saved.push(params);
      state.contents.set(file, content);
      state.mtimes.set(file, 222.25);
      if (created) {
        state.files = [
          ...state.files,
          {
            file,
            name: file.split("/").pop() ?? file,
            parse_ok: true,
            category_id: "my-cat",
            category_name: "我的品类",
            sources: 1,
            error: null,
          },
        ];
      }
      return {
        file,
        written: true,
        created,
        backed_up: created ? null : `${file}.bak`,
        mtime: 222.25,
        warnings: [],
      };
    },
    "yaml.delete": (params: never) => {
      const { file } = params as { file: string };
      state.deleted.push(file);
      state.files = state.files.filter((entry) => entry.file !== file);
      state.contents.delete(file);
      return { file, deleted: true, backed_up: `${file}.bak` };
    },
    doctor: (params: never) => {
      const { yamls } = params as { yamls?: string[] };
      return doctorOk(yamls?.[0] ?? AI, "AI 新闻", 2);
    },
    "run.status": () => ({ runs: [] }),
    "run.start": (params: never) => {
      const { yaml } = params as { yaml: string };
      state.started.push(params);
      return { run_id: 9, state: "running", yaml, dry: false, db: "myia.db" };
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
        throw errJson("method_not_found", `未知方法 ${args.method}`, "method");
      }
      return handler(args.params as never);
    },
  );
}

function lastCall(method: string): { params: unknown } | undefined {
  const calls = mocks.invoke.mock.calls.filter(([, args]) => (args as { method: string }).method === method);
  return calls.at(-1)?.[1] as { params: unknown } | undefined;
}

/**
 * 数据路由挂载(useBlocker 守卫的前提,与 main.tsx createHashRouter 同款形态):
 * /yaml-editor 挂屏本体 + 一条「侧栏替身」链接(与侧栏同机制的应用内 <Link>
 * 导航,供路由离开守卫用例点击);/logs 为去向屏替身。
 */
function renderScreen(initialPath = "/yaml-editor") {
  const router = createMemoryRouter(
    [
      {
        path: "/yaml-editor",
        element: (
          <>
            <YamlEditorScreen />
            <Link to="/logs">测试导航:切日志屏</Link>
          </>
        ),
      },
      { path: "/logs", element: <div data-testid="logs-screen">日志屏</div> },
    ],
    { initialEntries: [initialPath] },
  );
  return { router, ...render(<RouterProvider router={router} />) };
}

/** 编辑器替身(textarea) */
function editor(): HTMLTextAreaElement {
  return screen.getByLabelText("yaml-source") as HTMLTextAreaElement;
}

/** 断言用:按钮禁用态(本项目无 jest-dom,原生 disabled 直查) */
function isDisabled(button: HTMLElement): boolean {
  return (button as HTMLButtonElement).disabled;
}

/** 等文件列表就绪后选中某文件(点击左栏项) */
async function openByClick(file: string): Promise<void> {
  await screen.findByText("AI 新闻");
  fireEvent.click(itemButton(file));
}

/** 左栏文件项(点击选中) */
function itemButton(file: string): HTMLElement {
  return within(screen.getByTestId("yaml-file-list")).getByTitle(file).closest("button") as HTMLElement;
}

let confirmSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  mocks.invoke.mockReset();
  confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => {
  cleanup(); // vitest 非 globals 模式下 RTL 不自动清理,防 DOM 跨测试污染
  confirmSpy.mockRestore();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------

describe("配置编辑:文件列表与读取", () => {
  it("list 渲染:品类名/源数/坏文件损坏徽标;选中态", async () => {
    installSidecar(okSidecar().map);
    renderScreen();

    expect(await screen.findByText("AI 新闻")).toBeTruthy();
    expect(screen.getByText("2 源")).toBeTruthy();
    // 坏文件也入列(核心用例:修好 health 加载不了的文件)+ 损坏徽标 + 错误码
    expect(screen.getByText("损坏")).toBeTruthy();
    expect(screen.getByText("yaml_parse_error")).toBeTruthy();
    expect(screen.getByText("品类文件(2)")).toBeTruthy();

    fireEvent.click(itemButton(AI));
    await waitFor(() => {
      expect(editor().value).toContain("id: ai-news");
    });
    expect(itemButton(AI).getAttribute("aria-current")).toBe("true");
  });

  it("?file= 预选:源管理「编辑」跳转参数直接打开该文件 + 标题区完整路径", async () => {
    installSidecar(okSidecar().map);
    renderScreen(`/yaml-editor?file=${encodeURIComponent(AI)}`);

    await waitFor(() => {
      expect(editor().value).toContain("# 头注释:逐字节保真的原文");
    });
    expect(lastCall("yaml.read")?.params).toEqual({ file: AI });
    // 决议 10:完整路径可见(dev 模式即仓库 plugins/…,透明自担)
    expect(screen.getByTestId("editor-path").textContent).toBe(AI);
    // 读取只发生一次(预选不重复打开)
    expect(mocks.invoke.mock.calls.filter(([, args]) => (args as { method: string }).method === "yaml.read").length).toBe(1);
  });

  it("读取失败:结构化错误态 + 重试", async () => {
    const { map } = okSidecar();
    map["yaml.read"] = () => {
      throw errJson("file_not_found", "文件不存在: /repo/plugins/ghost.yaml", "params.file");
    };
    installSidecar(map);
    renderScreen(`/yaml-editor?file=${encodeURIComponent("/repo/plugins/ghost.yaml")}`);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("文件不存在");
    expect(alert.textContent).toContain("code=file_not_found");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => {
      expect(
        mocks.invoke.mock.calls.filter(([, args]) => (args as { method: string }).method === "yaml.read").length,
      ).toBeGreaterThanOrEqual(2);
    });
  });

  it("空目录:EmptyState + 「新建第一个品类」CTA(删光全部品类不是死胡同)", async () => {
    const { map } = okSidecar();
    map["yaml.list"] = () => ({ plugins_dir: DIR, files: [] });
    installSidecar(map);
    renderScreen();

    expect(await screen.findByText("还没有品类文件")).toBeTruthy();
    expect(screen.getByRole("button", { name: /新建第一个品类/ })).toBeTruthy();
  });
});

describe("配置编辑:dirty 守卫", () => {
  it("编辑未保存切换文件:confirm 取消留在当前文件;确认后丢弃并切换", async () => {
    installSidecar(okSidecar().map);
    renderScreen();
    await openByClick(AI);
    await waitFor(() => {
      expect(editor().value).toContain("id: ai-news");
    });

    fireEvent.change(editor(), { target: { value: AI_CONTENT.replace("*/15", "0 9") } });
    await waitFor(() => {
      expect(screen.getByTestId("editor-title").textContent).toContain("ai-news.yaml *");
    });

    confirmSpy.mockReturnValueOnce(false); // 取消:留在当前文件
    fireEvent.click(itemButton(BROKEN));
    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalled();
    });
    expect(editor().value).toContain("id: ai-news"); // 内容未丢
    expect(lastCall("yaml.read")?.params).toEqual({ file: AI }); // 没有发起新读取

    confirmSpy.mockReturnValue(true); // 确认:丢弃并切换
    fireEvent.click(itemButton(BROKEN));
    await waitFor(() => {
      expect(editor().value).toBe(BROKEN_CONTENT);
    });
    expect(lastCall("yaml.read")?.params).toEqual({ file: BROKEN });
  });

  it("路由离开守卫:干净态切屏直接放行,confirm 零调用", async () => {
    installSidecar(okSidecar().map);
    const { router } = renderScreen();
    await openByClick(AI);
    await waitFor(() => {
      expect(editor().value).toContain("id: ai-news");
    });

    fireEvent.click(screen.getByRole("link", { name: "测试导航:切日志屏" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/logs");
    });
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("路由离开守卫:dirty 时 SPA 内切屏 confirm;取消留在本屏内容不丢,确认后离开", async () => {
    installSidecar(okSidecar().map);
    const { router } = renderScreen();
    await openByClick(AI);
    await waitFor(() => {
      expect(editor().value).toContain("id: ai-news");
    });
    fireEvent.change(editor(), { target: { value: AI_CONTENT.replace("*/15", "0 9") } });
    await waitFor(() => {
      expect(screen.getByTestId("editor-title").textContent).toContain("ai-news.yaml *");
    });

    // 取消:留在本屏,编辑内容原样(未保存修改不随卸载静默丢)
    confirmSpy.mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole("link", { name: "测试导航:切日志屏" }));
    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/yaml-editor"); // 导航被拦
    });
    expect(editor().value).toContain("0 9 * * *"); // 草稿还在

    // 确认:离开本屏(用户明示放弃)
    confirmSpy.mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("link", { name: "测试导航:切日志屏" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/logs");
    });
    expect(await screen.findByTestId("logs-screen")).toBeTruthy();
  });
});

describe("配置编辑:校验干跑", () => {
  it("findings 分级:error 在前 warning 在后,message 逐条展示", async () => {
    const { map } = okSidecar();
    map["yaml.validate"] = () => ({
      valid: false,
      findings: [
        { path: "$.push[0]", code: "secret_unknown", message: "凭据 myia/push/token 尚未录入钥匙链(先 myia secret set)", level: "warning" },
        { path: "$.sources[0].url", code: "source_invalid", message: "源 url 不能为空", level: "error" },
      ],
      category: null,
    });
    installSidecar(map);
    renderScreen(`/yaml-editor?file=${encodeURIComponent(AI)}`);
    await waitFor(() => {
      expect(editor().value).toContain("id: ai-news");
    });

    fireEvent.click(screen.getByRole("button", { name: /校验/ }));
    const panel = await screen.findByTestId("findings-panel");
    const items = within(panel).getAllByRole("listitem");
    expect(items.length).toBe(2);
    expect(items[0].getAttribute("data-level")).toBe("error"); // error 级排前
    expect(items[0].textContent).toContain("source_invalid");
    expect(items[0].textContent).toContain("$.sources[0].url");
    expect(items[1].textContent).toContain("尚未录入钥匙链");
    expect(screen.queryByTestId("findings-empty")).toBeNull(); // 有 error 不显示「校验通过」
  });

  it("校验通过:一行通过态带品类摘要", async () => {
    installSidecar(okSidecar().map);
    renderScreen(`/yaml-editor?file=${encodeURIComponent(AI)}`);
    await waitFor(() => {
      expect(editor().value).toContain("id: ai-news");
    });

    fireEvent.click(screen.getByRole("button", { name: /校验/ }));
    expect(await screen.findByTestId("findings-empty")).toBeTruthy();
    expect(screen.getByTestId("findings-empty").textContent).toContain("校验通过:AI 新闻(2 源)");
  });
});

describe("配置编辑:保存流", () => {
  it("保存失败:结构化错误态,绝不假装成功", async () => {
    const { map } = okSidecar();
    map["yaml.save"] = () => {
      throw errJson("category_invalid", "内容未过品类校验(error 级 1 处),零写入", "params.content");
    };
    installSidecar(map);
    renderScreen(`/yaml-editor?file=${encodeURIComponent(AI)}`);
    await waitFor(() => {
      expect(editor().value).toContain("id: ai-news");
    });

    fireEvent.change(editor(), { target: { value: AI_CONTENT.replace("*/15", "0 9") } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("内容未过品类校验");
    expect(alert.textContent).toContain("code=category_invalid");
    expect(screen.queryByTestId("save-ok")).toBeNull();
  });

  it("保存成功:参数带 mtime 基线 → 清 dirty → 自动 doctor({yamls}) 复核展示", async () => {
    const { map, state } = okSidecar();
    installSidecar(map);
    renderScreen(`/yaml-editor?file=${encodeURIComponent(AI)}`);
    await waitFor(() => {
      expect(editor().value).toContain("id: ai-news");
    });

    const next = AI_CONTENT.replace("*/15 * * * *", "0 9 * * *");
    fireEvent.change(editor(), { target: { value: next } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    expect(await screen.findByTestId("save-ok")).toBeTruthy();
    const saveParams = lastCall("yaml.save")?.params as { file: string; content: string; expected_mtime: number };
    expect(saveParams.file).toBe(AI);
    expect(saveParams.content).toBe(next);
    expect(saveParams.expected_mtime).toBe(111.5); // read 带回的乐观锁基线
    expect(state.saved.length).toBe(1);
    // 决议 7:自动 doctor 复核 + 结果一行展示
    expect(lastCall("doctor")?.params).toEqual({ yamls: [AI] });
    expect(screen.getByTestId("doctor-check").textContent).toContain("doctor 复核通过:识别「AI 新闻」(2 源)");
    // dirty 清空:标题无 *;跑一次解禁
    await waitFor(() => {
      expect(screen.getByTestId("editor-title").textContent).not.toContain("*");
    });
    expect(isDisabled(screen.getByRole("button", { name: /跑一次/ }))).toBe(false);
  });

  it("保存成功:save 应答 warnings(secret_unknown)原样带回展示", async () => {
    const { map } = okSidecar();
    map["yaml.save"] = (params: never) => {
      const { file } = params as { file: string };
      return {
        file,
        written: true,
        created: false,
        backed_up: `${file}.bak`,
        mtime: 222.25,
        warnings: [
          { path: "$.push[0]", code: "secret_unknown", message: "凭据 myia/push/token 尚未录入钥匙链", level: "warning" },
        ],
      };
    };
    installSidecar(map);
    renderScreen(`/yaml-editor?file=${encodeURIComponent(AI)}`);
    await waitFor(() => {
      expect(editor().value).toContain("id: ai-news");
    });

    fireEvent.change(editor(), { target: { value: AI_CONTENT.replace("*/15", "0 9") } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    await screen.findByTestId("save-ok");
    expect(screen.getByText(/尚未录入钥匙链/)).toBeTruthy(); // warning 不拦保存但如实展示
  });

  it("Cmd+S 触发保存", async () => {
    const { map } = okSidecar();
    installSidecar(map);
    renderScreen(`/yaml-editor?file=${encodeURIComponent(AI)}`);
    await waitFor(() => {
      expect(editor().value).toContain("id: ai-news");
    });

    fireEvent.change(editor(), { target: { value: AI_CONTENT.replace("*/15", "0 9 * * *") } });
    fireEvent.keyDown(window, { key: "s", metaKey: true });

    expect(await screen.findByTestId("save-ok")).toBeTruthy();
    expect(lastCall("yaml.save")).toBeTruthy();
  });
});

describe("配置编辑:新建流", () => {
  it("stem 预检失败即拦(大写/空格):内联报因,不取模板;合法 stem → 模板草稿 → save(null mtime) → created", async () => {
    const { map, state } = okSidecar();
    installSidecar(map);
    renderScreen();
    await screen.findByText("AI 新闻");

    // 预检:大写与空格 → 内联错误,不发任何请求
    fireEvent.click(screen.getByRole("button", { name: /新建/ }));
    const input = screen.getByLabelText("新建文件名");
    fireEvent.change(input, { target: { value: "My Cat" } });
    expect(screen.getByRole("alert").textContent).toContain("文件名不合规");
    expect(isDisabled(screen.getByRole("button", { name: "创建草稿" }))).toBe(true);
    expect(lastCall("yaml.template")).toBeUndefined();

    // 合法 stem:取模板 → 回填 id → 未保存草稿态
    fireEvent.change(input, { target: { value: "my-cat" } });
    fireEvent.click(screen.getByRole("button", { name: "创建草稿" }));
    await waitFor(() => {
      expect(editor().value).toContain("id: my-cat");
    });
    expect(editor().value).toContain("# 最小品类模板"); // 模板注释原样进编辑器
    expect(lastCall("yaml.template")).toBeTruthy();
    expect(screen.getByText("未保存草稿")).toBeTruthy();
    expect(screen.getByTestId("draft-entry").textContent).toContain("my-cat.yaml *");

    // 保存走新建语义:expected_mtime=null;应答 created=true → 转正常编辑态
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));
    expect(await screen.findByText(/已保存\(新建\)/)).toBeTruthy();
    const saveParams = lastCall("yaml.save")?.params as { file: string; expected_mtime: number | null };
    expect(saveParams.file).toBe(`${DIR}/my-cat.yaml`);
    expect(saveParams.expected_mtime).toBeNull();
    await waitFor(() => {
      expect(screen.queryByText("未保存草稿")).toBeNull();
    });
    // 列表刷新:新品类入列(六份官方件 + 1 不混)
    expect(await screen.findByText("品类文件(3)")).toBeTruthy();
    expect(screen.getByText("我的品类")).toBeTruthy();
    expect(state.contents.has(`${DIR}/my-cat.yaml`)).toBe(true);
  });
});

describe("配置编辑:跑一次与采集运行提示", () => {
  it("跑一次:dirty 禁用(title 提示先保存),保存后发起 run.start 并引导日志屏", async () => {
    const { map, state } = okSidecar();
    installSidecar(map);
    renderScreen(`/yaml-editor?file=${encodeURIComponent(AI)}`);
    await waitFor(() => {
      expect(editor().value).toContain("id: ai-news");
    });

    const runButton = screen.getByRole("button", { name: /跑一次/ });
    expect(isDisabled(runButton)).toBe(false);
    fireEvent.change(editor(), { target: { value: AI_CONTENT.replace("*/15", "0 9") } });
    await waitFor(() => {
      expect(isDisabled(screen.getByRole("button", { name: /跑一次/ }))).toBe(true);
    });
    expect(screen.getByRole("button", { name: /跑一次/ }).getAttribute("title")).toContain("先保存");

    fireEvent.click(screen.getByRole("button", { name: /保存/ }));
    await screen.findByTestId("save-ok");
    fireEvent.click(screen.getByRole("button", { name: /跑一次/ }));

    expect(await screen.findByTestId("run-started")).toBeTruthy();
    expect(screen.getByTestId("run-started").textContent).toContain("run #9");
    expect(screen.getByRole("link", { name: "去日志屏查看" })).toBeTruthy();
    expect(lastCall("run.start")?.params).toEqual({ yaml: AI });
    expect(state.started.length).toBe(1);
  });

  it("采集运行中:保存前查 run.status,该品类 in-flight → 一行提示,只提示不拦", async () => {
    const { map } = okSidecar();
    map["run.status"] = () => ({
      runs: [
        {
          run_id: 5,
          yaml: AI,
          db: "myia.db",
          dry: false,
          state: "running",
          exit_code: null,
          status: null,
          started_at: "2026-10-03T09:00:00Z",
          finished_at: null,
          duration_ms: null,
          record: null,
        },
      ],
    });
    installSidecar(map);
    renderScreen(`/yaml-editor?file=${encodeURIComponent(AI)}`);
    await waitFor(() => {
      expect(editor().value).toContain("id: ai-news");
    });

    fireEvent.change(editor(), { target: { value: AI_CONTENT.replace("*/15", "0 9") } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    expect(await screen.findByTestId("run-notice")).toBeTruthy();
    expect(screen.getByTestId("run-notice").textContent).toContain("采集进行中(run #5)");
    expect(screen.getByTestId("run-notice").textContent).toContain("下一次运行生效");
    expect(await screen.findByTestId("save-ok")).toBeTruthy(); // 保存未被拦
    expect(lastCall("run.status")).toBeTruthy();
  });
});

describe("配置编辑:删除动作", () => {
  it("confirm 文案含 .bak 留底与官方件重建提示;取消不发删除;确认后删文件+刷新列表+选中项回 idle", async () => {
    const { map, state } = okSidecar();
    installSidecar(map);
    renderScreen();
    await openByClick(AI);
    await waitFor(() => {
      expect(editor().value).toContain("id: ai-news");
    });

    confirmSpy.mockReturnValueOnce(false); // 取消:零动作
    fireEvent.click(screen.getByRole("button", { name: "删除 ai-news.yaml" }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(state.deleted).toEqual([]);
    expect(lastCall("yaml.delete")).toBeUndefined();

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "删除 ai-news.yaml" }));
    const message = confirmSpy.mock.calls.at(-1)?.[0] ?? "";
    expect(message).toContain("AI 新闻");
    expect(message).toContain("ai-news.yaml.bak"); // .bak 留底
    expect(message).toContain("官方件删除后需重装或从模板重建"); // 决议 9 文案

    await waitFor(() => {
      expect(lastCall("yaml.delete")?.params).toEqual({ file: AI });
    });
    // 列表刷新:只剩坏文件;选中项被删 → 编辑器回 idle
    await waitFor(() => {
      expect(screen.getByText("品类文件(1)")).toBeTruthy();
    });
    expect(screen.queryByText("AI 新闻")).toBeNull();
    expect(screen.getByText("未选择文件")).toBeTruthy();
  });
});
