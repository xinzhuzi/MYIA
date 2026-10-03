// @vitest-environment jsdom
//
// 看图屏组件测试:mock sidecar(壳命令 sidecar_request 的 JS 假实现)+
// mock 插件 dialog/webview 拖放 + 捕获事件流订阅。覆盖:拖入自动 OCR(默认引擎
// 来自配置)/ 引擎切换即重跑 / 置信度色阶与低置信升二级 / 选择器路径导入 /
// 前端预检(格式/大小)/ OCR 结构化错误 / 二级看图事件流(starting→progress→
// completed,结果+复制)/ ask 必填 / 云端首切出网确认并记住 / completed 失败
// 结构化错误 / api 纯函数(色阶阈值·预检·方法映射)。
// 协议缺口(method_not_found)也是被测行为 —— image.* 未收编前如实呈现。
import { cleanup, fireEvent, render, screen, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  invoke: vi.fn(),
  open: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: mocks.open }));
vi.mock("@tauri-apps/api/webview", () => ({
  // Tauri 拖放路径路:订阅即回退订函数(无事件可发,与屏内降级一致)
  getCurrentWebview: () => ({ onDragDropEvent: vi.fn(async () => () => {}) }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, onSidecarEvent: vi.fn() };
});

const { onSidecarEvent } = await import("@/lib/api");
const onSidecarEventMock = vi.mocked(onSidecarEvent);

import { ImageScreen } from "./image-screen";
import {
  confTone,
  DEFAULT_VISION_CONFIG,
  extOf,
  importImageByBase64,
  importImageByPath,
  imageStatus,
  readFileAsDataUrl,
  readImageConfig,
  runImageOcr,
  saveImageConfig,
  startImageAnalyze,
  validateImageFile,
} from "./api";
import type {
  ImageAnalyzeResult,
  ImageOcrResult,
  SidecarErrorShape,
  SidecarEvent,
  VisionConfig,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// 协议夹具(形状逐字段对照 types.ts / 任务 design.md 协议表)
// ---------------------------------------------------------------------------

function configFixture(overrides: Partial<VisionConfig> = {}): VisionConfig {
  return {
    channel_default: "local",
    local: { base_url: "http://127.0.0.1:8080", model: "/models/qwen3-vl-8b-mlx" },
    cloud: { base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4.6v", api_key: null },
    ocr: { enabled: true, engine_default: "vision" },
    ...overrides,
  };
}

/** image.config.read 协议应答:{file, exists, config} 包装(entry.py 锁定形状) */
function configReadFixture(config: VisionConfig = configFixture()) {
  return { file: "/home/vision.yaml", exists: true, config };
}

function ocrFixture(engine: "vision" | "rapidocr"): ImageOcrResult {
  return {
    engine,
    ms: engine === "vision" ? 980 : 750,
    lines: [
      { text: "错误码 E1113", conf: 0.98 },
      { text: "订单 ID 88012345", conf: 0.42 },
      { text: "余额不足,请充值", conf: 0.75 },
    ],
  };
}

type Handler = (params: never) => unknown;
type SidecarMap = Record<string, Handler>;

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
        } satisfies SidecarErrorShape);
      }
      return handler(args.params as never);
    },
  );
}

function lastParamsOf(method: string): { params: unknown } | undefined {
  const calls = mocks.invoke.mock.calls.filter(([, args]) => (args as { method: string }).method === method);
  return calls.at(-1)?.[1] as { params: unknown } | undefined;
}

function pngFile(name = "shot.png", size = 1024): File {
  const content = size > 1024 ? new Uint8Array(size) : new Uint8Array([1, 2, 3]);
  return new File([content], name, { type: "image/png" });
}

/** 拖入一张图(HTML5 drop 路) */
function dropFile(file: File): void {
  fireEvent.drop(screen.getByTestId("image-dropzone"), { dataTransfer: { files: [file] } });
}

/** 事件流订阅句柄(onSidecarEvent 捕获) */
let emitEvent: ((event: SidecarEvent) => void) | null = null;

/** 内存 Storage:node 25 + vitest 4 的 jsdom 环境下 window.localStorage 被
 *  Node 实验性全局(缺方法)遮蔽,用可工作的 stub 保证出网知情记忆路径真实走到
 *  (与 feed-screen.test 同款处理)。 */
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
  mocks.invoke.mockReset();
  mocks.open.mockReset();
  onSidecarEventMock.mockReset();
  emitEvent = null;
  onSidecarEventMock.mockImplementation(async (handler) => {
    emitEvent = handler;
    return () => {};
  });
  vi.stubGlobal("localStorage", localStorageStub);
  localStorageStub.clear();
});
afterEach(() => {
  cleanup(); // vitest 非 globals 模式下 RTL 不自动清理,防 DOM 跨测试污染
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// 看图屏:导入 → 自动 OCR → 引擎切换
// ---------------------------------------------------------------------------

describe("看图屏:导入与一级 OCR", () => {
  it("拖入文件 → image.import(kind=base64)→ 自动 OCR(默认引擎来自配置)→ 逐行置信度色阶", async () => {
    installSidecar({
      "image.config.read": () => configReadFixture(),
      "image.import": () => ({ id: "abc123def4567890", path: "/data/images/abc123def4567890.png", bytes: 1234, ext: "png" }),
      "image.ocr": () => ocrFixture("vision"),
    });
    render(<ImageScreen />);

    dropFile(pngFile());
    expect(await screen.findByText("错误码 E1113")).toBeTruthy();

    // 导入参数:base64 + mime;OCR 自动触发且带配置默认引擎
    expect(lastParamsOf("image.import")?.params).toMatchObject({ kind: "base64", mime: "image/png" });
    expect(lastParamsOf("image.ocr")?.params).toEqual({ id: "abc123def4567890", engine: "vision" });
    // 结果标注来源引擎
    expect(screen.getByTestId("ocr-meta").textContent).toContain("来源 vision");

    // 色阶:0.98 高 / 0.75 中 / 0.42 低(标警示)
    const tones = screen.getAllByTestId("ocr-line").map((node) => node.getAttribute("data-conf-tone"));
    expect(tones).toEqual(["high", "low", "mid"]);
    // 低置信警示 + 手动升二级按钮(仅提示不自动)
    expect(screen.getByTestId("ocr-lowconf").textContent).toContain("1 行置信度 ≤ 0.5");
    expect(screen.getByTestId("ocr-escalate")).toBeTruthy();
  });

  it("引擎切换(Vision→RapidOCR)即重跑;结果改标新引擎(刻度注记不互比)", async () => {
    installSidecar({
      "image.config.read": () => configReadFixture(),
      "image.import": () => ({ id: "img-1", path: "/data/images/img-1.png", bytes: 10, ext: "png" }),
      "image.ocr": (params: never) => {
        const { engine } = params as { engine?: string };
        return ocrFixture(engine === "rapidocr" ? "rapidocr" : "vision");
      },
    });
    render(<ImageScreen />);
    dropFile(pngFile());
    expect(await screen.findByText("错误码 E1113")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "RapidOCR" }));
    await waitFor(() => {
      expect(screen.getByTestId("ocr-meta").textContent).toContain("来源 rapidocr");
    });
    expect(lastParamsOf("image.ocr")?.params).toEqual({ id: "img-1", engine: "rapidocr" });
  });

  it("系统选择器路径导入(kind=path);路径入口无缩略图占位说明", async () => {
    mocks.open.mockResolvedValue("/Users/u/报错截图.png");
    installSidecar({
      "image.config.read": () => configReadFixture(),
      "image.import": () => ({ id: "img-2", path: "/data/images/img-2.png", bytes: 4096, ext: "png" }),
      "image.ocr": () => ocrFixture("vision"),
    });
    render(<ImageScreen />);

    fireEvent.click(screen.getByTestId("image-dropzone"));
    expect(await screen.findByText("错误码 E1113")).toBeTruthy();
    expect(mocks.open).toHaveBeenCalled(); // 选择器走 dialog 插件
    expect(lastParamsOf("image.import")?.params).toEqual({ kind: "path", value: "/Users/u/报错截图.png" });
    // 路径导入:无文件内容 → 占位说明(最小权限,无 fs 读)
    expect(screen.getByText(/路径导入:缩略图不可见/)).toBeTruthy();
    expect(screen.getByText("报错截图.png")).toBeTruthy();
  });

  it("前端预检:非法格式 / 超 10MB → 零协议调用,结构化提示", async () => {
    installSidecar({ "image.config.read": () => configFixture() });
    render(<ImageScreen />);

    dropFile(new File([new Uint8Array([1])], "notes.txt", { type: "text/plain" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("不支持的格式 .txt");
    expect(alert.textContent).toContain("code=image_unsupported");

    fireEvent.drop(screen.getByTestId("image-dropzone"), {
      dataTransfer: { files: [pngFile("big.png", 11 * 1024 * 1024)] },
    });
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toContain("超过 10MB");
    });
    expect(mocks.invoke.mock.calls.some(([, args]) => (args as { method: string }).method === "image.import")).toBe(false);
  });

  it("OCR 失败 → 结构化错误态(code=path=);重试再发 image.ocr", async () => {
    let failures = 0;
    installSidecar({
      "image.config.read": () => configReadFixture(),
      "image.import": () => ({ id: "img-3", path: "/x/img-3.png", bytes: 1, ext: "png" }),
      "image.ocr": () => {
        failures += 1;
        throw JSON.stringify({ code: "image_ocr_failed", path: "$", message: "OCR 引擎执行失败" });
      },
    });
    render(<ImageScreen />);
    dropFile(pngFile());

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("OCR 失败:OCR 引擎执行失败");
    expect(alert.textContent).toContain("code=image_ocr_failed");
    expect(failures).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => {
      expect(failures).toBe(2);
    });
  });

  it("配置读取失败(method_not_found 协议缺口)→ 注记降级缺省,主流程仍可用", async () => {
    installSidecar({
      "image.import": () => ({ id: "img-4", path: "/x/img-4.png", bytes: 1, ext: "png" }),
      "image.ocr": () => ocrFixture("vision"),
    });
    render(<ImageScreen />);

    await screen.findByText(/看图配置读取失败\(code=method_not_found/);
    dropFile(pngFile());
    expect(await screen.findByText("错误码 E1113")).toBeTruthy(); // 缺省引擎 vision 照跑
  });
});

// ---------------------------------------------------------------------------
// 看图屏:二级看图(事件流 / 通道知情 / 模式)
// ---------------------------------------------------------------------------

describe("看图屏:二级看图事件流", () => {
  function installAnalyzeSidecar() {
    installSidecar({
      "image.config.read": () => configReadFixture(),
      "image.import": () => ({ id: "img-1", path: "/x/img-1.png", bytes: 1, ext: "png" }),
      "image.ocr": () => ocrFixture("vision"),
      "image.analyze": () => ({ job_id: 7 }) satisfies ImageAnalyzeResult,
      "image.status": () => ({ busy: false }),
    });
  }

  async function importImageAndReady() {
    render(<ImageScreen />);
    dropFile(pngFile());
    await screen.findByText("错误码 E1113");
  }

  it("读字校对:启动 → progress(模型阶段)→ completed;结果渲染 + 复制成功态", async () => {
    installAnalyzeSidecar();
    await importImageAndReady();

    fireEvent.click(screen.getByTestId("vision-start"));
    await waitFor(() => {
      expect(lastParamsOf("image.analyze")?.params).toEqual({ id: "img-1", mode: "read", channel: "local" });
    });

    // 进行态:progress 事件驱动(stage=model + pct)
    await waitFor(() => {
      expect(screen.getByTestId("vision-running")).toBeTruthy();
    });
    await act(async () => {
      emitEvent?.({ type: "image.progress", job_id: 7, stage: "model", pct: 40 });
    });
    expect(screen.getByTestId("vision-running").textContent).toContain("视觉模型解读中");

    // completed:结果 + 元信息(模型/通道/耗时/已用 OCR 初稿)
    await act(async () => {
      emitEvent?.({
        type: "image.completed",
        job_id: 7,
        ok: true,
        result: { text: "校对后的正确文本", model: "/models/qwen3-vl-8b-mlx", channel: "local", elapsed_ms: 13000, ocr_used: true },
      });
    });
    expect(screen.getByTestId("vision-result").textContent).toContain("校对后的正确文本");
    expect(screen.getByTestId("vision-result").textContent).toContain("已用 OCR 初稿");

    // 复制:clipboard 可用 → 已复制(注:OcrPanel 低置信框同为 role=status,按文案定位)
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    fireEvent.click(screen.getByTestId("vision-copy"));
    await screen.findByText("已复制");
    expect(writeText).toHaveBeenCalledWith("校对后的正确文本");
  });

  it("completed 失败 → 结构化错误态;image_unreachable 附启动指引", async () => {
    installAnalyzeSidecar();
    await importImageAndReady();

    fireEvent.click(screen.getByTestId("vision-start"));
    await waitFor(() => {
      expect(screen.getByTestId("vision-running")).toBeTruthy();
    });
    await act(async () => {
      emitEvent?.({
        type: "image.completed",
        job_id: 7,
        ok: false,
        error: { code: "image_unreachable", path: "$", message: "本地视觉端点不可达: http://127.0.0.1:8080" },
      });
    });
    const box = screen.getByTestId("vision-error");
    expect(box.textContent).toContain("本地视觉端点不可达");
    expect(box.textContent).toContain("code=image_unreachable");
    expect(box.textContent).toContain("启动指引");
  });

  it("completed 在订阅建立前被丢 → 订阅就绪后经 image.status 对账恢复(不卡进行中)", async () => {
    // 瞬时失败场景(如 OCR 毫秒级失败):completed 事件先于订阅写出被丢 ——
    // 全程零 emitEvent,错误态必须仍能经 image.status 携带的 last 终态出现
    installSidecar({
      "image.config.read": () => configReadFixture(),
      "image.import": () => ({ id: "img-1", path: "/x/img-1.png", bytes: 1, ext: "png" }),
      "image.ocr": () => ocrFixture("vision"),
      "image.analyze": () => ({ job_id: 9 }) satisfies ImageAnalyzeResult,
      "image.status": () => ({
        busy: false,
        last: {
          type: "image.completed",
          job_id: 9,
          ok: false,
          error: { code: "image_ocr_failed", path: "$", message: "read 模式 OCR 初稿失败: 引擎依赖缺失" },
        },
      }),
    });
    await importImageAndReady();

    fireEvent.click(screen.getByTestId("vision-start"));
    const box = await screen.findByTestId("vision-error", {}, { timeout: 3000 });
    expect(box.textContent).toContain("read 模式 OCR 初稿失败");
    expect(box.textContent).toContain("code=image_ocr_failed");
    // 对账按 job_id 精确查询(终态自带 job_id,不匹配即忽略)
    expect(lastParamsOf("image.status")?.params).toEqual({ job_id: 9 });
  });

  it("analyze 立即拒绝(image_busy)→ 结构化错误态", async () => {
    installSidecar({
      "image.config.read": () => configReadFixture(),
      "image.import": () => ({ id: "img-1", path: "/x/img-1.png", bytes: 1, ext: "png" }),
      "image.ocr": () => ocrFixture("vision"),
      "image.analyze": () => {
        throw JSON.stringify({ code: "image_busy", path: "$", message: "已有看图任务在执行 job_id=3" });
      },
    });
    await importImageAndReady();

    fireEvent.click(screen.getByTestId("vision-start"));
    const box = await screen.findByTestId("vision-error");
    expect(box.textContent).toContain("已有看图任务在执行");
    expect(box.textContent).toContain("code=image_busy");
  });

  it("ask 模式:问题必填(空则启动禁用);填后携带 question", async () => {
    installAnalyzeSidecar();
    await importImageAndReady();

    fireEvent.click(screen.getByRole("tab", { name: "自由提问" }));
    expect(screen.getByLabelText("提问")).toBeTruthy();
    expect((screen.getByTestId("vision-start") as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("提问"), { target: { value: "图里的订单号是多少?" } });
    await waitFor(() => {
      expect((screen.getByTestId("vision-start") as HTMLButtonElement).disabled).toBe(false);
    });
    fireEvent.click(screen.getByTestId("vision-start"));
    await waitFor(() => {
      expect(lastParamsOf("image.analyze")?.params).toEqual({
        id: "img-1",
        mode: "ask",
        question: "图里的订单号是多少?",
        channel: "local",
      });
    });
  });

  it("切云端首次弹出网确认;确认记住后直切;启动走 cloud 通道", async () => {
    installAnalyzeSidecar();
    await importImageAndReady();

    // 首切云端:拦下弹知情确认
    fireEvent.click(screen.getByRole("tab", { name: "云端" }));
    expect(screen.getByTestId("cloud-consent").textContent).toContain("图片内容将出网");
    expect(screen.queryByRole("tab", { name: "云端" })?.getAttribute("aria-selected")).toBe("false"); // 未确认不切换

    fireEvent.click(screen.getByTestId("cloud-consent-confirm"));
    await waitFor(() => {
      expect(screen.queryByRole("tab", { name: "云端" })?.getAttribute("aria-selected")).toBe("true");
    });
    expect(localStorageStub.getItem("myia.image.cloudConsent.v1")).toBe("acknowledged");

    // 已记住:本地↔云端来回切不再弹
    fireEvent.click(screen.getByRole("tab", { name: "本地" }));
    fireEvent.click(screen.getByRole("tab", { name: "云端" }));
    expect(screen.queryByTestId("cloud-consent")).toBeNull();

    // 云端启动:channel=cloud
    fireEvent.click(screen.getByTestId("vision-start"));
    await waitFor(() => {
      expect(lastParamsOf("image.analyze")?.params).toMatchObject({ channel: "cloud", mode: "read" });
    });
  });

  it("低置信升二级:点击 → read 模式直接启动(manual,非自动)", async () => {
    installAnalyzeSidecar();
    await importImageAndReady();

    expect(screen.getByRole("tab", { name: "读字校对" }).getAttribute("aria-selected")).toBe("true"); // 默认已是 read
    fireEvent.click(screen.getByTestId("vision-start")); // 先切到 ask 制造非 read 态
    await waitFor(() => {
      expect(screen.getByTestId("vision-running")).toBeTruthy();
    });
    // 任务在跑:切模式被禁用 —— 等任务结束再验证升二级
    await act(async () => {
      emitEvent?.({ type: "image.completed", job_id: 7, ok: true, result: { text: "x", model: "m", channel: "local", elapsed_ms: 1, ocr_used: false } });
    });
    fireEvent.click(screen.getByRole("tab", { name: "自由提问" }));
    expect(screen.getByRole("tab", { name: "读字校对" }).getAttribute("aria-selected")).toBe("false");

    fireEvent.click(screen.getByTestId("ocr-escalate")); // 升二级按钮
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "读字校对" }).getAttribute("aria-selected")).toBe("true");
    });
    await waitFor(() => {
      expect(lastParamsOf("image.analyze")?.params).toMatchObject({ mode: "read" });
    });
  });
});

// ---------------------------------------------------------------------------
// api 模块:纯函数与协议方法映射
// ---------------------------------------------------------------------------

describe("看图 api:色阶 / 预检 / 方法映射", () => {
  it("confTone 阈值:≤0.5 low / ≤0.9 mid / 其余 high(两引擎统一阈值)", () => {
    expect(confTone(0.3)).toBe("low");
    expect(confTone(0.5)).toBe("low");
    expect(confTone(0.51)).toBe("mid");
    expect(confTone(0.9)).toBe("mid");
    expect(confTone(0.91)).toBe("high");
    expect(confTone(1)).toBe("high");
  });

  it("validateImageFile:白名单格式 + 10MB 上限;extOf 大写归一", () => {
    expect(validateImageFile({ name: "a.png", size: 100 })).toBeNull();
    expect(validateImageFile({ name: "b.heic", size: 100 })).toBeNull();
    expect(validateImageFile({ name: "c.txt", size: 100 })).toContain("不支持的格式");
    expect(validateImageFile({ name: "d.png", size: 10 * 1024 * 1024 + 1 })).toContain("10MB");
    expect(validateImageFile({ name: "noext", size: 100 })).toContain("不支持的格式");
    expect(extOf("Shot.PNG")).toBe("png");
    expect(extOf("archive.tar.gz")).toBe("gz");
  });

  it("readFileAsDataUrl:File → data URL + 裸 base64", async () => {
    const { dataUrl, base64, mime } = await readFileAsDataUrl(pngFile());
    expect(dataUrl.startsWith("data:image/png;base64,")).toBe(true);
    expect(baseUrlLength(base64)).toBeGreaterThan(0);
    expect(mime).toBe("image/png");
  });

  it("协议方法映射:六个 image.* 方法名与参数形状逐一对齐", async () => {
    mocks.invoke.mockResolvedValue({});
    await importImageByPath("/tmp/a.png");
    expect(mocks.invoke).toHaveBeenLastCalledWith("sidecar_request", {
      method: "image.import",
      params: { kind: "path", value: "/tmp/a.png" },
    });

    await importImageByBase64("QUJD", "image/png");
    expect(mocks.invoke).toHaveBeenLastCalledWith("sidecar_request", {
      method: "image.import",
      params: { kind: "base64", value: "QUJD", mime: "image/png" },
    });

    await runImageOcr("img-1", "rapidocr");
    expect(mocks.invoke).toHaveBeenLastCalledWith("sidecar_request", {
      method: "image.ocr",
      params: { id: "img-1", engine: "rapidocr" },
    });
    await runImageOcr("img-1");
    expect(mocks.invoke).toHaveBeenLastCalledWith("sidecar_request", {
      method: "image.ocr",
      params: { id: "img-1" }, // engine 缺省:sidecar 取配置默认
    });

    await startImageAnalyze({ id: "img-1", mode: "ask", question: "问", channel: "cloud" });
    expect(mocks.invoke).toHaveBeenLastCalledWith("sidecar_request", {
      method: "image.analyze",
      params: { id: "img-1", mode: "ask", question: "问", channel: "cloud" },
    });

    await imageStatus();
    expect(mocks.invoke).toHaveBeenLastCalledWith("sidecar_request", { method: "image.status", params: {} });
    await imageStatus(9); // 对账查询:携带 job_id
    expect(mocks.invoke).toHaveBeenLastCalledWith("sidecar_request", {
      method: "image.status",
      params: { job_id: 9 },
    });

    await readImageConfig();
    expect(mocks.invoke).toHaveBeenLastCalledWith("sidecar_request", { method: "image.config.read", params: {} });

    const config = configFixture();
    await saveImageConfig(config);
    expect(mocks.invoke).toHaveBeenLastCalledWith("sidecar_request", {
      method: "image.config.save",
      params: { config },
    });
  });

  it("invoke 抛结构化错误 JSON → 归一为 SidecarRequestError(code/path/message)", async () => {
    mocks.invoke.mockRejectedValue(
      JSON.stringify({ code: "image_too_large", path: "params.value", message: "图片超过 10MB" }),
    );
    await expect(importImageByPath("/tmp/big.png")).rejects.toMatchObject({
      code: "image_too_large",
      path: "params.value",
      message: "图片超过 10MB",
    });
  });

  it("readImageConfig:解包 {file, exists, config} 协议包装,返裸 VisionConfig", async () => {
    const config = configFixture();
    mocks.invoke.mockResolvedValue(configReadFixture(config));
    await expect(readImageConfig()).resolves.toEqual(config);
    // 形状不符(mock/异常应答缺 config):原样透传,由调用方形状防御如实呈现
    mocks.invoke.mockResolvedValue(undefined);
    const raw = await readImageConfig();
    expect(raw).toBeUndefined();
  });

  it("DEFAULT_VISION_CONFIG 与 Python 缺省对齐(settings.py:local base_url 带 /v1 后缀)", () => {
    expect(DEFAULT_VISION_CONFIG.local.base_url).toBe("http://127.0.0.1:8080/v1");
    expect(DEFAULT_VISION_CONFIG.cloud.base_url).toBe("https://open.bigmodel.cn/api/paas/v4");
    expect(DEFAULT_VISION_CONFIG.cloud.model).toBe("glm-4.6v");
    expect(DEFAULT_VISION_CONFIG.ocr.engine_default).toBe("vision");
  });
});

function baseUrlLength(base64: string): number {
  return base64.length;
}
