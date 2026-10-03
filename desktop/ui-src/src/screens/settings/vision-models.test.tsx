// @vitest-environment jsdom
//
// 设置 → 看图分区 · 模型管理卡(10-03-vision-v2)测试:mock sidecar invoke
// (image.models.list/download/delete/activate + image.server.status/ensure 六方法,
// 与 entry.py `_HANDLERS` 同名对账)+ mock 事件 listen(image.models.progress /
// completed 与 image.server.completed 三事件经 onSidecarEvent 订阅)。覆盖:
// 挂载清单与 active/incomplete 徽标 / 下载事件流(提交 → 进度条吃 progress →
// completed 刷新清单)/ 下载失败(completed ok:false error 上屏)/ 前端预校验
// (repo 形状)/ 删除二次确认 / activate / ensure(慢路径应答即返+事件翻徽章、
// 快路径应答即终态、事件收口失败、ensure_busy)/ 半成品禁激活 /
// remount job 驻留恢复 / 字节格式化。
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ invoke: vi.fn(), listen: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));

import { VisionModelsCard, formatModelBytes } from "./vision-form";
import type { VisionModelEntry } from "@/lib/api";

const GB = 1024 ** 3;

function modelFixture(overrides: Partial<VisionModelEntry> = {}): VisionModelEntry {
  return {
    name: "Qwen2.5-VL-7B-Instruct-4bit",
    path: "/home/models/Qwen2.5-VL-7B-Instruct-4bit",
    bytes: 4.2 * GB,
    active: false,
    incomplete: false,
    ...overrides,
  };
}

interface SidecarModelsState {
  models: VisionModelEntry[];
  server: { running: boolean; base_url: string; model: string; healthy: boolean };
  ensureStarted: boolean;
}

/** 有状态 mock:delete/activate 真改 models,ensure 真翻 server(reredad 可见) */
function installSidecar(models: VisionModelEntry[] = []): SidecarModelsState {
  const state: SidecarModelsState = {
    models,
    server: { running: false, base_url: "http://127.0.0.1:8080/v1", model: models.find((m) => m.active)?.path ?? "", healthy: false },
    ensureStarted: false,
  };
  mocks.invoke.mockImplementation(async (_command: string, args: { method: string; params?: unknown }) => {
    switch (args.method) {
      case "image.models.list":
        return { models: state.models };
      case "image.models.download":
        return { job_id: 7 };
      case "image.models.delete": {
        const { name } = args.params as { name: string };
        state.models = state.models.filter((model) => model.name !== name);
        return { ok: true };
      }
      case "image.models.activate": {
        const { name } = args.params as { name: string };
        state.models = state.models.map((model) => ({ ...model, active: model.name === name }));
        return { ok: true };
      }
      case "image.server.status":
        return { running: state.server.running, base_url: state.server.base_url, model: state.server.model, healthy: state.server.healthy };
      case "image.server.ensure": {
        // 慢路径契约(10-03-vision-v2 复查):应答即返快照超集(ensuring+job_id),
        // 自起+健康等待跑 sidecar 后台线程,终态走 image.server.completed 事件
        // (测试内由用例主动 emit 事件收口,这里不再同步翻 server)
        return { running: false, base_url: state.server.base_url, model: state.server.model, healthy: false, started: false, ensuring: true, job_id: 5 };
      }
      default:
        throw JSON.stringify({ code: "method_not_found", path: "method", message: `未知方法 ${args.method}` });
    }
  });
  return state;
}

function callsOf(method: string): unknown[] {
  return mocks.invoke.mock.calls
    .filter(([, args]) => (args as { method: string }).method === method)
    .map(([, args]) => (args as { params?: unknown }).params);
}

/** 事件通道:onSidecarEvent → listen("sidecar://event", handler);捕获 handler 供测试注入 */
let emitSidecarEvent: ((payload: unknown) => void) | undefined;

/** Tauri listen 回调的事件包装形状(payload = SidecarEvent) */
interface TauriEventWrapper {
  event: string;
  id: number;
  payload: unknown;
}

beforeEach(() => {
  mocks.invoke.mockReset();
  mocks.listen.mockReset();
  emitSidecarEvent = undefined;
  mocks.listen.mockImplementation(async (_name: string, handler: (event: TauriEventWrapper) => void) => {
    emitSidecarEvent = (payload: unknown) => handler({ event: "sidecar://event", id: 0, payload });
    return () => {
      emitSidecarEvent = undefined;
    };
  });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("设置 · 模型管理卡:挂载与清单", () => {
  it("挂载:image.models.list + image.server.status 各拉一次;清单出名/体积/当前徽标/设为当前;服务徽章按 status 呈现", async () => {
    installSidecar([
      modelFixture({ active: true }),
      modelFixture({ name: "qwen3-vl-8b-mlx", bytes: 8.1 * GB, active: false }),
    ]);
    render(<VisionModelsCard />);

    const activeRow = await screen.findByTestId("vision-model-Qwen2.5-VL-7B-Instruct-4bit");
    expect(activeRow.textContent).toContain("Qwen2.5-VL-7B-Instruct-4bit");
    expect(activeRow.textContent).toContain("4.2 GB");
    expect(within(activeRow).getByText("当前")).toBeTruthy();
    // active 模型删除钮禁用(后端 model_active_refused 同口径前置)
    const activeDelete = within(activeRow).getByRole("button", { name: "删除模型 Qwen2.5-VL-7B-Instruct-4bit" }) as HTMLButtonElement;
    expect(activeDelete.disabled).toBe(true);

    const otherRow = screen.getByTestId("vision-model-qwen3-vl-8b-mlx");
    expect(otherRow.textContent).toContain("8.1 GB");
    expect(within(otherRow).getByRole("button", { name: "设为当前" })).toBeTruthy();
    const otherDelete = within(otherRow).getByRole("button", { name: "删除模型 qwen3-vl-8b-mlx" }) as HTMLButtonElement;
    expect(otherDelete.disabled).toBe(false);

    // 服务行:status {running:false} → 未运行徽章;base_url 注记行
    expect((await screen.findByTestId("vision-server-badge")).textContent).toBe("未运行");
    expect(screen.getByText(/http:\/\/127\.0\.0\.1:8080\/v1/)).toBeTruthy();

    // 协议方法名对账(六方法中的两读方法;与 entry.py `_HANDLERS` 同名)
    expect(mocks.invoke).toHaveBeenCalledWith("sidecar_request", { method: "image.models.list", params: {} });
    expect(mocks.invoke).toHaveBeenCalledWith("sidecar_request", { method: "image.server.status", params: {} });
  });

  it("空清单 = 合法空态(下载引导文案,非错误)", async () => {
    installSidecar([]);
    render(<VisionModelsCard />);
    expect(await screen.findByTestId("vision-models-empty")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("清单读取失败 → 注记降级(role=note 不占告警位,code 如实呈现)", async () => {
    mocks.invoke.mockImplementation(async (_command: string, args: { method: string }) => {
      if (args.method === "image.models.list") {
        throw JSON.stringify({ code: "internal_error", path: "$", message: "扫描失败" });
      }
      if (args.method === "image.server.status") return { running: false, base_url: "http://127.0.0.1:8080/v1", model: "", healthy: false };
      throw JSON.stringify({ code: "method_not_found", path: "method", message: `未知方法 ${args.method}` });
    });
    render(<VisionModelsCard />);
    const note = await screen.findByTestId("vision-models-note");
    expect(note.getAttribute("role")).toBe("note");
    expect(note.textContent).toContain("code=internal_error");
    expect(note.textContent).toContain("扫描失败");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("设置 · 模型管理卡:下载事件流", () => {
  it("提交 → image.models.download {repo,name?} → progress 事件喂进度条 → completed ok 刷新清单", async () => {
    installSidecar([modelFixture({ active: true })]);
    render(<VisionModelsCard />);
    await screen.findByTestId("vision-model-Qwen2.5-VL-7B-Instruct-4bit");

    fireEvent.change(screen.getByLabelText("模型 repo"), {
      target: { value: "mlx-community/Qwen2.5-VL-7B-Instruct-4bit" },
    });
    fireEvent.change(screen.getByLabelText("模型本地名"), { target: { value: "qwen25-vl-7b" } });
    fireEvent.click(screen.getByRole("button", { name: "下载模型" }));

    await waitFor(() => expect(callsOf("image.models.download")).toContainEqual({ repo: "mlx-community/Qwen2.5-VL-7B-Instruct-4bit", name: "qwen25-vl-7b" }));
    const progress = await screen.findByTestId("vision-download-progress");
    expect(screen.getByRole("button", { name: "下载中…" })).toBeTruthy(); // 单飞:job 态禁用

    // 陌生 job 的 progress 不喂本卡(job_id 过滤)
    act(() => {
      emitSidecarEvent?.({ type: "image.models.progress", job_id: 99, repo: "mlx-community/other", done_bytes: 1, total_bytes: 2, ts: "2026-10-03T00:00:00Z" });
    });
    expect(progress.textContent).not.toContain("50%");

    // 本 job 首报无总量:不定态文案(只有已下载字节,零百分比)
    act(() => {
      emitSidecarEvent?.({ type: "image.models.progress", job_id: 7, repo: "mlx-community/Qwen2.5-VL-7B-Instruct-4bit", done_bytes: 512, ts: "2026-10-03T00:00:00Z" });
    });
    await waitFor(() => expect(screen.getByTestId("vision-download-progress").textContent).toContain("总量未知"));
    expect(screen.getByTestId("vision-download-progress").textContent).toContain("512 B");

    // 次报补总量:4GB / 8GB = 50%(total 一旦回报即粘住)
    act(() => {
      emitSidecarEvent?.({ type: "image.models.progress", job_id: 7, repo: "mlx-community/Qwen2.5-VL-7B-Instruct-4bit", done_bytes: 4 * GB, total_bytes: 8 * GB, ts: "2026-10-03T00:00:01Z" });
    });
    await waitFor(() => expect(screen.getByTestId("vision-download-progress").textContent).toContain("50%"));
    expect(screen.getByTestId("vision-download-progress").textContent).toContain("4.0 GB / 8.0 GB");
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("50");

    // completed ok → 状态注记 + 清单刷新(list 再次被调)+ 下载按钮回到可点
    const listCallsBefore = callsOf("image.models.list").length;
    act(() => {
      emitSidecarEvent?.({ type: "image.models.completed", job_id: 7, ok: true, ts: "2026-10-03T00:00:02Z" });
    });
    expect(await screen.findByTestId("vision-download-status")).toBeTruthy();
    await waitFor(() => expect(callsOf("image.models.list").length).toBeGreaterThan(listCallsBefore));
    expect(screen.queryByTestId("vision-download-progress")).toBeNull();
    expect(screen.getByRole("button", { name: "下载模型" })).toBeTruthy();
  });

  it("completed ok:false → 结构化 error code 上屏(如 disk_insufficient)", async () => {
    installSidecar();
    render(<VisionModelsCard />);
    await screen.findByTestId("vision-server-badge");

    fireEvent.change(screen.getByLabelText("模型 repo"), {
      target: { value: "mlx-community/Qwen2.5-VL-7B-Instruct-4bit" },
    });
    fireEvent.click(screen.getByRole("button", { name: "下载模型" }));
    await screen.findByTestId("vision-download-progress");

    act(() => {
      emitSidecarEvent?.({ type: "image.models.completed", job_id: 7, ok: false, error: "disk_insufficient", ts: "2026-10-03T00:00:00Z" });
    });
    const error = await screen.findByTestId("vision-download-error");
    expect(error.getAttribute("role")).toBe("alert");
    expect(error.textContent).toContain("disk_insufficient");
    expect(screen.queryByTestId("vision-download-progress")).toBeNull();
  });

  it("前端预校验:repo 非 mlx-community/<name> 形状 → 拦下,零协议下载调用", async () => {
    installSidecar();
    render(<VisionModelsCard />);
    await screen.findByTestId("vision-server-badge");

    fireEvent.change(screen.getByLabelText("模型 repo"), { target: { value: "openai/gpt-4o" } });
    fireEvent.click(screen.getByRole("button", { name: "下载模型" }));

    expect(await screen.findByTestId("vision-download-error")).toBeTruthy();
    expect(screen.getByTestId("vision-download-error").textContent).toContain("mlx-community");
    expect(callsOf("image.models.download")).toEqual([]);
  });

  it("提交被拒(download_busy 单飞)→ code:message 上屏", async () => {
    mocks.invoke.mockImplementation(async (_command: string, args: { method: string }) => {
      if (args.method === "image.models.download") {
        throw JSON.stringify({ code: "download_busy", path: "$", message: "已有模型下载在执行 job_id=3(单飞)" });
      }
      if (args.method === "image.models.list") return { models: [] };
      if (args.method === "image.server.status") return { running: false, base_url: "http://127.0.0.1:8080/v1", model: "", healthy: false };
      throw JSON.stringify({ code: "method_not_found", path: "method", message: `未知方法 ${args.method}` });
    });
    render(<VisionModelsCard />);
    await screen.findByTestId("vision-server-badge");

    fireEvent.change(screen.getByLabelText("模型 repo"), { target: { value: "mlx-community/Qwen2.5-VL-7B-Instruct-4bit" } });
    fireEvent.click(screen.getByRole("button", { name: "下载模型" }));

    const error = await screen.findByTestId("vision-download-error");
    expect(error.textContent).toContain("download_busy");
  });
});

describe("设置 · 模型管理卡:删除 / 激活", () => {
  it("删除走二次确认:确认后 image.models.delete {name} → 清单刷新;取消零调用", async () => {
    installSidecar([
      modelFixture({ active: true }),
      modelFixture({ name: "old-vl", bytes: 1024, active: false }),
    ]);
    render(<VisionModelsCard />);
    const row = await screen.findByTestId("vision-model-old-vl");

    fireEvent.click(within(row).getByRole("button", { name: "删除模型 old-vl" }));
    fireEvent.click(within(row).getByTestId("vision-model-delete-confirm-old-vl"));

    await waitFor(() => expect(callsOf("image.models.delete")).toContainEqual({ name: "old-vl" }));
    await waitFor(() => expect(screen.queryByTestId("vision-model-old-vl")).toBeNull());
  });

  it("删除取消:confirm 态点取消零协议调用", async () => {
    installSidecar([modelFixture({ name: "old-vl", active: false })]);
    render(<VisionModelsCard />);
    const row = await screen.findByTestId("vision-model-old-vl");

    fireEvent.click(within(row).getByRole("button", { name: "删除模型 old-vl" }));
    fireEvent.click(within(row).getByRole("button", { name: "取消" }));
    expect(callsOf("image.models.delete")).toEqual([]);
    expect(within(row).getByRole("button", { name: "删除模型 old-vl" })).toBeTruthy();
  });

  it("激活:image.models.activate {name} → 清单 active 徽标移动 + 状态注记", async () => {
    installSidecar([
      modelFixture({ name: "a-vl", active: true }),
      modelFixture({ name: "b-vl", active: false }),
    ]);
    render(<VisionModelsCard />);
    const rowB = await screen.findByTestId("vision-model-b-vl");

    fireEvent.click(within(rowB).getByRole("button", { name: "设为当前" }));

    await waitFor(() => expect(callsOf("image.models.activate")).toContainEqual({ name: "b-vl" }));
    expect(await screen.findByTestId("vision-activate-status")).toBeTruthy();
    // 有状态 mock:b 变当前,a 失活(清单刷新后可见)
    await waitFor(() => {
      expect(within(screen.getByTestId("vision-model-b-vl")).getByText("当前")).toBeTruthy();
      expect(within(screen.getByTestId("vision-model-a-vl")).getByRole("button", { name: "设为当前" })).toBeTruthy();
    });
  });
});

describe("设置 · 模型管理卡:本地 server 代管行", () => {
  it("确保启动(慢路径):应答即返 ensuring → image.server.completed 事件翻徽章 + 注记", async () => {
    installSidecar([modelFixture({ active: true })]);
    render(<VisionModelsCard />);
    expect((await screen.findByTestId("vision-server-badge")).textContent).toBe("未运行");

    fireEvent.click(screen.getByRole("button", { name: "确保启动" }));

    expect(mocks.invoke).toHaveBeenCalledWith("sidecar_request", { method: "image.server.ensure", params: {} });
    // 应答即返:等待态徽章(不再同步等 2 分钟健康窗)
    await waitFor(() => expect(screen.getByTestId("vision-server-badge").textContent).toBe("确保启动中…"));

    // 终态事件:ok=true + status(started=true = 本次自起)→ 徽章翻健康
    act(() => {
      emitSidecarEvent?.({
        type: "image.server.completed",
        job_id: 5,
        ok: true,
        status: { running: true, base_url: "http://127.0.0.1:8080/v1", model: "", healthy: true, started: true },
        ts: "2026-10-03T00:00:00Z",
      });
    });
    expect((await screen.findByTestId("vision-server-badge")).textContent).toBe("运行中 · 健康");
    const status = await screen.findByTestId("vision-ensure-status");
    expect(status.getAttribute("role")).toBe("status");
    expect(status.textContent).toContain("已自起");
    expect(status.textContent).toContain("2 分钟");
    expect(screen.getByRole("button", { name: "确保启动" })).toBeTruthy(); // 等待态解除
  });

  it("确保启动(快路径):已健康 = 应答即终态,零事件直接翻徽章", async () => {
    mocks.invoke.mockImplementation(async (_command: string, args: { method: string }) => {
      if (args.method === "image.server.ensure") {
        return { running: true, base_url: "http://127.0.0.1:8080/v1", model: "", healthy: true, started: false };
      }
      if (args.method === "image.models.list") return { models: [] };
      if (args.method === "image.server.status") return { running: false, base_url: "http://127.0.0.1:8080/v1", model: "", healthy: false };
      throw JSON.stringify({ code: "method_not_found", path: "method", message: `未知方法 ${args.method}` });
    });
    render(<VisionModelsCard />);
    expect((await screen.findByTestId("vision-server-badge")).textContent).toBe("未运行");

    fireEvent.click(screen.getByRole("button", { name: "确保启动" }));

    expect((await screen.findByTestId("vision-server-badge")).textContent).toBe("运行中 · 健康");
    await screen.findByTestId("vision-ensure-status");
    expect(screen.queryByTestId("vision-ensure-status")?.textContent).toContain("已在运行");
  });

  it("确保启动失败(no_local_model,事件收口)→ 结构化错误上屏", async () => {
    mocks.invoke.mockImplementation(async (_command: string, args: { method: string }) => {
      if (args.method === "image.server.ensure") {
        return { running: false, base_url: "http://127.0.0.1:8080/v1", model: "", healthy: false, started: false, ensuring: true, job_id: 6 };
      }
      if (args.method === "image.models.list") return { models: [] };
      if (args.method === "image.server.status") return { running: false, base_url: "http://127.0.0.1:8080/v1", model: "", healthy: false };
      throw JSON.stringify({ code: "method_not_found", path: "method", message: `未知方法 ${args.method}` });
    });
    render(<VisionModelsCard />);
    await screen.findByTestId("vision-server-badge");

    fireEvent.click(screen.getByRole("button", { name: "确保启动" }));
    await waitFor(() => expect(screen.getByTestId("vision-server-badge").textContent).toBe("确保启动中…"));

    act(() => {
      emitSidecarEvent?.({ type: "image.server.completed", job_id: 6, ok: false, error: "no_local_model", ts: "2026-10-03T00:00:00Z" });
    });

    const box = await screen.findByRole("alert");
    expect(box.textContent).toContain("no_local_model");
    // 等待态解除(按钮回可点,事件已收口)
    expect(screen.getByRole("button", { name: "确保启动" })).toBeTruthy();
  });

  it("确保启动被拒(ensure_busy 单飞)→ 同步结构化错误上屏", async () => {
    mocks.invoke.mockImplementation(async (_command: string, args: { method: string }) => {
      if (args.method === "image.server.ensure") {
        throw JSON.stringify({ code: "ensure_busy", path: "$", message: "已有确保启动在执行 job_id=3(单飞)" });
      }
      if (args.method === "image.models.list") return { models: [] };
      if (args.method === "image.server.status") return { running: false, base_url: "http://127.0.0.1:8080/v1", model: "", healthy: false };
      throw JSON.stringify({ code: "method_not_found", path: "method", message: `未知方法 ${args.method}` });
    });
    render(<VisionModelsCard />);
    await screen.findByTestId("vision-server-badge");

    fireEvent.click(screen.getByRole("button", { name: "确保启动" }));

    const box = await screen.findByRole("alert");
    expect(box.textContent).toContain("ensure_busy");
  });
});

describe("设置 · 模型管理卡:半成品模型(incomplete)", () => {
  it("半成品标「未完成(可续传)」徽章,设为当前禁用;完整模型不受影响", async () => {
    installSidecar([
      modelFixture({ name: "half-vl", bytes: 1024, incomplete: true }),
      modelFixture({ name: "full-vl", bytes: 2048, incomplete: false }),
    ]);
    render(<VisionModelsCard />);
    const halfRow = await screen.findByTestId("vision-model-half-vl");
    expect(within(halfRow).getByText("未完成(可续传)")).toBeTruthy();
    const halfActivate = within(halfRow).getByRole("button", { name: "设为当前" }) as HTMLButtonElement;
    expect(halfActivate.disabled).toBe(true); // 后端 model_incomplete 同口径前置

    const fullRow = screen.getByTestId("vision-model-full-vl");
    expect(within(fullRow).queryByText("未完成(可续传)")).toBeNull();
    const fullActivate = within(fullRow).getByRole("button", { name: "设为当前" }) as HTMLButtonElement;
    expect(fullActivate.disabled).toBe(false);
  });
});

describe("设置 · 模型管理卡:下载 job remount 断链修复", () => {
  it("下载中卸载重挂:job 态经模块级驻留恢复,progress/completed 事件续喂", async () => {
    installSidecar([]);
    const view = render(<VisionModelsCard />);
    await screen.findByTestId("vision-server-badge");
    fireEvent.change(screen.getByLabelText("模型 repo"), {
      target: { value: "mlx-community/Qwen2.5-VL-7B-Instruct-4bit" },
    });
    fireEvent.click(screen.getByRole("button", { name: "下载模型" }));
    expect(await screen.findByTestId("vision-download-progress")).toBeTruthy();
    expect(screen.getByRole("button", { name: "下载中…" })).toBeTruthy();

    view.unmount();
    render(<VisionModelsCard />);
    // 重挂载即恢复等待态(模块级 activeDownloadJob;下载按钮单飞禁用同在)
    const progress = await screen.findByTestId("vision-download-progress");
    expect(screen.getByRole("button", { name: "下载中…" })).toBeTruthy();

    // 本 job progress 续喂(jobId 从模块驻留恢复,事件过滤不丢)
    act(() => {
      emitSidecarEvent?.({ type: "image.models.progress", job_id: 7, repo: "mlx-community/Qwen2.5-VL-7B-Instruct-4bit", done_bytes: 512, ts: "2026-10-03T00:00:00Z" });
    });
    await waitFor(() => expect(screen.getByTestId("vision-download-progress").textContent).toContain("512 B"));
    expect(progress.textContent).toContain("Qwen2.5-VL-7B-Instruct-4bit");

    // completed 收口:等待态解除(模块驻留清空,不串扰后续用例)
    act(() => {
      emitSidecarEvent?.({ type: "image.models.completed", job_id: 7, ok: true, ts: "2026-10-03T00:00:01Z" });
    });
    await waitFor(() => expect(screen.queryByTestId("vision-download-progress")).toBeNull());
    expect(screen.getByRole("button", { name: "下载模型" })).toBeTruthy();
  });
});

describe("formatModelBytes(体积展示)", () => {
  it("B/KB/MB/GB 阶梯与一位小数", () => {
    expect(formatModelBytes(0)).toBe("0 B");
    expect(formatModelBytes(512)).toBe("512 B");
    expect(formatModelBytes(2048)).toBe("2.0 KB");
    expect(formatModelBytes(3 * 1024 ** 2)).toBe("3.0 MB");
    expect(formatModelBytes(4.2 * GB)).toBe("4.2 GB");
    expect(formatModelBytes(Number.NaN)).toBe("—");
  });
});
