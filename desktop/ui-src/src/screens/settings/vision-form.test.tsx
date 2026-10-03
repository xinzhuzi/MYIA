// @vitest-environment jsdom
//
// 设置 → 看图分区(VisionForm)测试:mock sidecar(image.config.read/save +
// secret.set/secret.list)。覆盖:配置读取填充表单 / 保存分流(结构字段走
// image.config.save,云端 key 只走 secret.set 入钥匙链、配置仅留 keychain: 引用、
// 值零回显零落盘)/ 保存后重读复核一致 / key 未输入保留原引用 / base_url 前端
// 校验拦截 / 配置读取失败(协议未收编)注记降级不占告警位。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));

import { VisionForm } from "./vision-form";
import type { SecretSetParams, VisionConfig } from "@/lib/api";

function configFixture(overrides: Partial<VisionConfig> = {}): VisionConfig {
  return {
    channel_default: "local",
    local: { base_url: "http://127.0.0.1:8080", model: "" },
    cloud: { base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4.6v", api_key: null },
    ocr: { enabled: true, engine_default: "vision" },
    ...overrides,
  };
}

interface SidecarState {
  config: VisionConfig;
  secrets: Map<string, string>;
}

/** 有状态 mock:save 真改 config,reread 立即可见(复核往返的诚实模拟) */
function installSidecar(initial: VisionConfig = configFixture()): SidecarState {
  const state: SidecarState = { config: initial, secrets: new Map() };
  mocks.invoke.mockImplementation(
    async (_command: string, args: { method: string; params?: unknown }) => {
      switch (args.method) {
        case "image.config.read":
          return state.config;
        case "image.config.save": {
          const { config } = args.params as { config: VisionConfig };
          state.config = config;
          return { ok: true };
        }
        case "secret.set": {
          const { name, value } = args.params as SecretSetParams;
          state.secrets.set(name, value);
          return { name, stored: true };
        }
        case "secret.list":
          return { names: [...state.secrets.keys()].sort() };
        default:
          throw JSON.stringify({
            code: "method_not_found",
            path: "method",
            message: `未知方法 ${args.method}`,
          });
      }
    },
  );
  return state;
}

function callsOf(method: string): unknown[] {
  return mocks.invoke.mock.calls
    .filter(([, args]) => (args as { method: string }).method === method)
    .map(([, args]) => (args as { params: unknown }).params);
}

beforeEach(() => {
  mocks.invoke.mockReset();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("设置 · 看图分区:读取与保存分流", () => {
  it("读取配置填充表单(本地 base_url/模型路径/云端模型默认值直见)", async () => {
    installSidecar(
      configFixture({
        local: { base_url: "http://127.0.0.1:1234", model: "/models/qwen3-vl-8b-mlx" },
        cloud: { base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4.6v", api_key: "keychain:myia/image/api_key" },
      }),
    );
    render(<VisionForm secretNames={["myia/image/api_key"]} />);

    await waitFor(() => {
      expect((screen.getByLabelText("本地 base_url") as HTMLInputElement).value).toBe("http://127.0.0.1:1234");
    });
    expect((screen.getByLabelText("本地模型路径") as HTMLInputElement).value).toBe("/models/qwen3-vl-8b-mlx");
    expect((screen.getByLabelText("云端模型") as HTMLInputElement).value).toBe("glm-4.6v");
    expect(screen.getByText("key 已在钥匙链")).toBeTruthy(); // secret.list 名单核验
  });

  it("保存:结构字段走 image.config.save;key 只走 secret.set,配置仅留 keychain: 引用;key 零回显保存即清", async () => {
    const state = installSidecar();
    render(<VisionForm secretNames={null} />);

    await waitFor(() => {
      expect((screen.getByLabelText("本地 base_url") as HTMLInputElement).value).toBe("http://127.0.0.1:8080");
    });
    fireEvent.change(screen.getByLabelText("本地 base_url"), { target: { value: "http://127.0.0.1:1234" } });
    fireEvent.change(screen.getByLabelText("本地模型路径"), { target: { value: "/models/qwen3-vl-8b-mlx" } });
    fireEvent.change(screen.getByLabelText("云端模型"), { target: { value: "glm-4.6v" } });
    fireEvent.change(screen.getByLabelText("云端 API Key"), { target: { value: "sk-img-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "保存看图配置" }));

    expect(await screen.findByTestId("vision-save-status")).toBeTruthy();
    // 凭据:key 只经 secret.set 入钥匙链(值不回程)
    expect(callsOf("secret.set")).toContainEqual({ name: "myia/image/api_key", value: "sk-img-secret" });
    expect(state.secrets.get("myia/image/api_key")).toBe("sk-img-secret");
    // 结构配置:整份写回,api_key 字段只有 keychain: 引用(明文拒载铁律)
    const saves = callsOf("image.config.save") as { config: VisionConfig }[];
    expect(saves).toHaveLength(1);
    expect(saves[0].config).toEqual({
      channel_default: "local",
      local: { base_url: "http://127.0.0.1:1234", model: "/models/qwen3-vl-8b-mlx" },
      cloud: { base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4.6v", api_key: "keychain:myia/image/api_key" },
      ocr: { enabled: true, engine_default: "vision" },
    });
    // 复核一致(reread 与所写同值);key 输入清空;明文不出现在任何 DOM
    expect(screen.getByTestId("vision-save-status").textContent).toContain("重读复核一致");
    expect((screen.getByLabelText("云端 API Key") as HTMLInputElement).value).toBe("");
    expect(document.body.textContent).not.toContain("sk-img-secret");
  });

  it("key 未输入:不调 secret.set,原 keychain 引用原样保留", async () => {
    const state = installSidecar(
      configFixture({ cloud: { base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4.6v", api_key: "keychain:myia/image/api_key" } }),
    );
    render(<VisionForm secretNames={["myia/image/api_key"]} />);
    await waitFor(() => {
      expect((screen.getByLabelText("本地 base_url") as HTMLInputElement).value).toBe("http://127.0.0.1:8080");
    });

    fireEvent.click(screen.getByRole("button", { name: "保存看图配置" }));
    await screen.findByTestId("vision-save-status");

    expect(callsOf("secret.set")).toEqual([]);
    const saves = callsOf("image.config.save") as { config: VisionConfig }[];
    expect(saves[0].config.cloud.api_key).toBe("keychain:myia/image/api_key");
    expect(state.config.cloud.api_key).toBe("keychain:myia/image/api_key");
  });

  it("base_url 非 http(s) → 前端校验拦下,零协议写调用", async () => {
    installSidecar();
    render(<VisionForm secretNames={null} />);
    await waitFor(() => {
      expect((screen.getByLabelText("本地 base_url") as HTMLInputElement).value).toBe("http://127.0.0.1:8080");
    });
    fireEvent.change(screen.getByLabelText("本地 base_url"), { target: { value: "ftp://nope" } });
    fireEvent.click(screen.getByRole("button", { name: "保存看图配置" }));

    expect(await screen.findByText(/本地 base_url 须为 http\(s\) 地址/)).toBeTruthy();
    expect(callsOf("image.config.save")).toEqual([]);
    expect(callsOf("secret.set")).toEqual([]);
  });

  it("配置读取失败(协议未收编 method_not_found)→ 注记降级(role=note,不占告警位)", async () => {
    mocks.invoke.mockImplementation(async (_command: string, args: { method: string }) => {
      throw JSON.stringify({ code: "method_not_found", path: "method", message: `未知方法 ${args.method}` });
    });
    render(<VisionForm secretNames={null} />);

    const note = await screen.findByTestId("vision-config-note");
    expect(note.getAttribute("role")).toBe("note");
    expect(note.textContent).toContain("code=method_not_found");
    expect(screen.queryByRole("alert")).toBeNull(); // 挂载期零告警(设置屏 alert 单匹配用例不受扰)
  });
});
