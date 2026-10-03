// @vitest-environment jsdom
//
// 源管理屏「当场编辑」弹窗组件测试:mock sidecar(壳命令 sidecar_request 的
// JS 假实现)+ mock @uiw/react-codemirror(textarea 受控替身,惯例同
// yaml-editor-screen.test.tsx)。覆盖:打开即读原文(标题/完整路径/模态语义)/
// 编辑+保存成功(mtime 基线 + doctor 复核行)/ 保存失败结构化错误 /
// mtime_conflict 给「重读」/ dirty 关闭守卫(ESC 与遮罩,取消留内容确认才关)/
// 干净态直接关(confirm 零调用)/ Meta+S 触发保存。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

import { YamlEditorDialog } from "./yaml-editor-dialog";
import type { DoctorResult } from "@/lib/api";

const FILE = "/repo/plugins/ai-news.yaml";
const CONTENT = `# 头注释:逐字节保真的原文\nid: ai-news\nname: AI 新闻\nschedule: "*/15 * * * *"\nsources:\n  - name: hacker-news\n    url: "https://example.com/news"\n`;

// ---------------------------------------------------------------------------
// 夹具(形状逐字段对照 ./api.ts 契约 / desktop/entry.py `_m_yaml_*`)
// ---------------------------------------------------------------------------

function doctorOk(file: string, name: string, sources: number): DoctorResult {
  return {
    command: "doctor",
    generated_at: "2026-10-03T12:00:00.000Z",
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
          health: { state: "ok", reason: "", observed: 0, latest: null, baseline: null },
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
// mock sidecar:内存态 = 单文件内容 + mtime
// ---------------------------------------------------------------------------

type Handler = (params: never) => unknown;
type SidecarMap = Record<string, Handler>;

/** 结构化协议错误(与 Rust Err(String) 同形态:JSON 文本) */
function errJson(code: string, message: string, path = "$"): string {
  return JSON.stringify({ code, path, message });
}

function okSidecar() {
  const state = {
    content: CONTENT,
    saved: [] as unknown[],
  };
  const map: SidecarMap = {
    "yaml.read": () => ({ file: FILE, content: state.content, size: 123, mtime: 111.5 }),
    "yaml.validate": () => ({
      valid: true,
      findings: [],
      category: { id: "ai-news", name: "AI 新闻", sources: 1 },
    }),
    "yaml.save": (params: never) => {
      const { file, content } = params as { file: string; content: string };
      state.saved.push(params);
      state.content = content;
      return {
        file,
        written: true,
        created: false,
        backed_up: `${file}.bak`,
        mtime: 222.25,
        warnings: [],
      };
    },
    doctor: () => doctorOk(FILE, "AI 新闻", 1),
    "run.status": () => ({ runs: [] }),
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

function callCount(method: string): number {
  return mocks.invoke.mock.calls.filter(([, args]) => (args as { method: string }).method === method).length;
}

function renderDialog() {
  const onClose = vi.fn();
  render(<YamlEditorDialog file={FILE} onClose={onClose} />);
  return { onClose };
}

/** 编辑器替身(textarea) */
function editor(): HTMLTextAreaElement {
  return screen.getByLabelText("yaml-source") as HTMLTextAreaElement;
}

/** 等弹窗读盘就绪(编辑器出现且带原文) */
async function openAndWait(): Promise<void> {
  await waitFor(() => {
    expect(editor().value).toContain("# 头注释:逐字节保真的原文");
  });
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

describe("编辑弹窗:打开与读取", () => {
  it("打开即读原文:模态语义 + 标题文件名 + 完整路径小字 + 注释原样进编辑器", async () => {
    installSidecar(okSidecar().map);
    renderDialog();

    const dialog = await screen.findByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label")).toBe("编辑 ai-news.yaml");
    expect(lastCall("yaml.read")?.params).toEqual({ file: FILE });

    await openAndWait();
    expect(screen.getByTestId("editor-title").textContent).toContain("ai-news.yaml");
    // 决议 10:完整路径可见(dev 模式即仓库 plugins/…,透明自担)
    expect(screen.getByTestId("editor-path").textContent).toBe(FILE);
    expect(screen.getByTestId("editor-title").textContent).not.toContain("*"); // 干净态
  });

  it("读取失败:结构化错误态 + 重试重读", async () => {
    const { map } = okSidecar();
    map["yaml.read"] = () => {
      throw errJson("file_not_found", "文件不存在: /repo/plugins/ghost.yaml", "params.file");
    };
    installSidecar(map);
    renderDialog();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("文件不存在");
    expect(alert.textContent).toContain("code=file_not_found");

    map["yaml.read"] = () => ({ file: FILE, content: CONTENT, size: 123, mtime: 111.5 });
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await openAndWait();
  });
});

describe("编辑弹窗:保存流", () => {
  it("编辑 + 保存成功:参数带 mtime 基线 → dirty 清 → doctor 复核行出现", async () => {
    const { map, state } = okSidecar();
    installSidecar(map);
    renderDialog();
    await openAndWait();

    const next = CONTENT.replace("*/15 * * * *", "0 9 * * *");
    fireEvent.change(editor(), { target: { value: next } });
    expect(screen.getByTestId("editor-title").textContent).toContain("ai-news.yaml *");

    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    expect(await screen.findByTestId("save-ok")).toBeTruthy();
    const saveParams = lastCall("yaml.save")?.params as { file: string; content: string; expected_mtime: number };
    expect(saveParams.file).toBe(FILE);
    expect(saveParams.content).toBe(next);
    expect(saveParams.expected_mtime).toBe(111.5); // read 带回的乐观锁基线
    expect(state.saved.length).toBe(1);
    // 决议 7:自动 doctor 复核 + 结果一行展示
    expect(lastCall("doctor")?.params).toEqual({ yamls: [FILE] });
    expect(screen.getByTestId("doctor-check").textContent).toContain("doctor 复核通过:识别「AI 新闻」(1 源)");
    await waitFor(() => {
      expect(screen.getByTestId("editor-title").textContent).not.toContain("*");
    });
  });

  it("保存失败:结构化错误如实展示,绝不假装成功", async () => {
    const { map } = okSidecar();
    map["yaml.save"] = () => {
      throw errJson("category_invalid", "内容未过品类校验(error 级 1 处),零写入", "params.content");
    };
    installSidecar(map);
    renderDialog();
    await openAndWait();

    fireEvent.change(editor(), { target: { value: CONTENT.replace("*/15", "0 9") } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("内容未过品类校验");
    expect(alert.textContent).toContain("code=category_invalid");
    expect(screen.queryByTestId("save-ok")).toBeNull();
    expect(screen.queryByTestId("doctor-check")).toBeNull();
  });

  it("mtime_conflict:ErrorBox 给「重读」;确认后重读拉回最新原文(不覆盖外部改动)", async () => {
    const { map } = okSidecar();
    map["yaml.save"] = () => {
      throw errJson("mtime_conflict", "文件在读取后被外部修改,请重读后再保存", "params.expected_mtime");
    };
    installSidecar(map);
    renderDialog();
    await openAndWait();

    fireEvent.change(editor(), { target: { value: CONTENT.replace("*/15", "0 9") } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("mtime_conflict");
    expect(alert.textContent).toContain("外部修改");
    fireEvent.click(within(alert).getByRole("button", { name: "重读" }));

    // confirm(dirty 守卫)→ 重新 yaml.read → 编辑器回到盘上原文
    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(editor().value).toBe(CONTENT);
    });
    expect(callCount("yaml.read")).toBe(2);
    await waitFor(() => {
      expect(screen.getByTestId("editor-title").textContent).not.toContain("*");
    });
  });

  it("Meta+S 触发保存", async () => {
    const { map } = okSidecar();
    installSidecar(map);
    renderDialog();
    await openAndWait();

    fireEvent.change(editor(), { target: { value: CONTENT.replace("*/15 * * * *", "0 9 * * *") } });
    fireEvent.keyDown(window, { key: "s", metaKey: true });

    expect(await screen.findByTestId("save-ok")).toBeTruthy();
    expect(lastCall("yaml.save")).toBeTruthy();
  });
});

describe("编辑弹窗:关闭守卫", () => {
  it("干净态直接关:ESC / 关闭按钮均不 confirm,onClose 即回调", async () => {
    installSidecar(okSidecar().map);
    const { onClose } = renderDialog();
    await openAndWait();

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1);
    });
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("dirty 时关闭(ESC/遮罩/关闭按钮)先 confirm:取消留在弹窗内容不丢,确认才关", async () => {
    installSidecar(okSidecar().map);
    const { onClose } = renderDialog();
    await openAndWait();
    fireEvent.change(editor(), { target: { value: CONTENT.replace("*/15", "0 9") } });
    await waitFor(() => {
      expect(screen.getByTestId("editor-title").textContent).toContain("*");
    });

    // ESC 取消:弹窗还在,内容未丢,onClose 未回调
    confirmSpy.mockReturnValueOnce(false);
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalled();
    });
    expect(screen.getByTestId("yaml-editor-dialog")).toBeTruthy();
    expect(editor().value).toContain("0 9 * * *");
    expect(onClose).not.toHaveBeenCalled();

    // 遮罩点击取消:同守卫
    confirmSpy.mockReturnValueOnce(false);
    fireEvent.click(screen.getByTestId("yaml-editor-overlay"));
    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalledTimes(2);
    });
    expect(onClose).not.toHaveBeenCalled();

    // 底部「关闭」按钮确认:放弃未保存修改,onClose 回调
    confirmSpy.mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    await waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1);
    });
    const message = confirmSpy.mock.calls.at(-1)?.[0] ?? "";
    expect(message).toContain("未保存的修改");
  });
});
