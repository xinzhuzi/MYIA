// @vitest-environment jsdom
//
// 设置组件测试:mock sidecar(secret.set / secret.list / doctor 的 JS 假实现),
// 覆盖:LLM 凭据保存(只经 secret.set 入钥匙链、值零回显、保存即清)/
// env: 引用不经界面写 / 表单校验 / secret.set 失败结构化错误 /
// doctor 回显(凭据存在性 + enrich 现值 + findings)/ 推送凭据保存 / 代理池探测。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));

import { SettingsScreen } from "./settings-screen";
import type {
  CredentialEntry,
  DoctorPluginReport,
  DoctorResult,
  EnrichSection,
  Finding,
  PluginReport,
  ProxyPoolStatus,
  SecretSetParams,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// 协议夹具(types.ts 逐字段对照)
// ---------------------------------------------------------------------------

function pluginFixture(file: string, overrides?: Partial<DoctorPluginReport>): DoctorPluginReport {
  const base: PluginReport = {
    file,
    id: "stocks",
    name: "量化快讯",
    schedule: "*/30 * * * *",
    timezone: "Asia/Shanghai",
    push_channels: ["feishu_card", "telegram"],
    loaded: true,
    load_errors: null,
    sources: [],
  };
  return { ...base, next_fire_at: null, enrich: null, ...overrides };
}

function enrichFixture(model: string): EnrichSection {
  return {
    enabled: true,
    model,
    scores: ["value", "relevance", "credibility"],
    batch: 8,
    cache: true,
    budget_per_run: 40,
    cache_rows: 12,
  };
}

function credentialFixture(overrides?: Partial<CredentialEntry>): CredentialEntry {
  return {
    kind: "keychain",
    name: "myia/stocks/tg_token",
    ref: "keychain:myia/stocks/tg_token",
    paths: [{ plugin: "stocks", path: "$.push[1].target" }],
    plugins: ["stocks"],
    exists: true,
    ...overrides,
  };
}

function doctorFixture(overrides?: {
  plugins?: DoctorPluginReport[];
  credentials?: CredentialEntry[];
  findings?: Finding[];
  pools?: ProxyPoolStatus[];
  config?: string | null;
}): DoctorResult {
  const findings = overrides?.findings ?? [];
  return {
    command: "doctor",
    generated_at: "2026-10-02T10:00:00.000Z",
    db: "myia.db",
    // 与 cli.py 同口径:healthy = 无 error 级 finding
    healthy: !findings.some((finding) => finding.severity === "error"),
    plugins: overrides?.plugins ?? [],
    credentials: {
      backend_available: true,
      backend_error: null,
      entries: overrides?.credentials ?? [],
    },
    proxy: { config: overrides?.config ?? null, pools: overrides?.pools ?? [] },
    findings,
    summary: { plugins: overrides?.plugins?.length ?? 0, sources: 0, errors: 0, warnings: 0 },
  };
}

// ---------------------------------------------------------------------------
// mock sidecar:secret.set 记名记值(值只在断言里用,绝不进 DOM)、doctor 可编程
// ---------------------------------------------------------------------------

interface SidecarState {
  secrets: Map<string, string>;
  doctor: (params: { config?: string }) => DoctorResult;
}

function installSidecar(doctorImpl?: (params: { config?: string }) => DoctorResult) {
  const state: SidecarState = {
    secrets: new Map(),
    doctor: doctorImpl ?? (() => doctorFixture()),
  };
  mocks.invoke.mockImplementation(
    async (_command: string, args: { method: string; params?: unknown }) => {
      switch (args.method) {
        case "secret.set": {
          const { name, value } = args.params as SecretSetParams;
          state.secrets.set(name, value);
          return { name, stored: true };
        }
        case "secret.list":
          return { names: [...state.secrets.keys()].sort() };
        case "doctor":
          return state.doctor((args.params ?? {}) as { config?: string });
        default:
          throw JSON.stringify({
            code: "method_not_found",
            path: "method",
            message: `未知方法 ${args.method}`,
            data: { allowed: ["doctor", "secret.list", "secret.set"] },
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

async function typeByLabel(label: string, value: string): Promise<void> {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

beforeEach(() => {
  mocks.invoke.mockReset();
});
afterEach(() => {
  cleanup(); // vitest 非 globals 模式下 RTL 不自动清理,防 DOM 跨测试污染
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------

describe("设置:LLM 凭据保存(钥匙链唯一路径,零回显)", () => {
  it("base_url+key 各写一个规范名;保存即清 key;值不出现在任何 DOM", async () => {
    const state = installSidecar();
    render(<SettingsScreen />);
    await typeByLabel("base_url", "https://open.bigmodel.cn/api/paas/v4");
    await typeByLabel("model", "glm-4-flash");
    await typeByLabel("LLM API Key", "sk-secret-123456");
    fireEvent.click(screen.getByRole("button", { name: "保存 LLM 凭据" }));

    await screen.findByTestId("save-status");
    // secret.set 两次:名字规范、值原样过协议(值只在协议侧,不回程)
    const setCalls = callsOf("secret.set") as SecretSetParams[];
    expect(setCalls).toContainEqual({ name: "myia/llm/base_url", value: "https://open.bigmodel.cn/api/paas/v4" });
    expect(setCalls).toContainEqual({ name: "myia/llm/api_key", value: "sk-secret-123456" });
    expect(state.secrets.get("myia/llm/api_key")).toBe("sk-secret-123456");
    // 铁律:值零回显 —— 整个 DOM 找不到 key 明文;key 输入框已清空
    expect(document.body.textContent).not.toContain("sk-secret-123456");
    expect((screen.getByLabelText("LLM API Key") as HTMLInputElement).value).toBe("");
    // 状态行只报名字
    expect(screen.getByTestId("save-status").textContent).toContain("myia/llm/api_key");
    expect(screen.getByTestId("save-status").textContent).toContain("myia/llm/base_url");
  });

  it("base_url 为 env: 引用 → 不经界面写(无 secret.set),给出入 YAML 提示", async () => {
    const state = installSidecar();
    render(<SettingsScreen />);
    await typeByLabel("base_url", "env:MYIA_LLM_BASE_URL");
    await typeByLabel("model", "glm-4-flash");
    fireEvent.click(screen.getByRole("button", { name: "保存 LLM 凭据" }));

    await waitFor(() => {
      expect(screen.getByText(/请把该引用直接写入品类 YAML/)).toBeTruthy();
    });
    expect(callsOf("secret.set")).toEqual([]); // 引用不是值,不写钥匙链
    expect(state.secrets.size).toBe(0);
  });

  it("model 为空不拦凭据保存(model 不经界面持久化,只有凭据走 secret.set)", async () => {
    installSidecar();
    render(<SettingsScreen />);
    await typeByLabel("base_url", "https://api.example.com");
    fireEvent.click(screen.getByRole("button", { name: "保存 LLM 凭据" }));

    await screen.findByTestId("save-status");
    // 只有 base_url 入钥匙链;model 不产生任何写调用
    expect(callsOf("secret.set")).toEqual([
      { name: "myia/llm/base_url", value: "https://api.example.com" },
    ]);
  });

  it("base_url 非法(既非 URL 也非引用)→ 前端校验拦下,零写调用", async () => {
    installSidecar();
    render(<SettingsScreen />);
    await typeByLabel("base_url", "ftp://not-allowed");
    fireEvent.click(screen.getByRole("button", { name: "保存 LLM 凭据" }));

    expect(await screen.findByText(/base_url 须为 http\(s\) 地址或 env:\/keychain: 引用/)).toBeTruthy();
    expect(callsOf("secret.set")).toEqual([]);
  });

  it("secret.set 失败 → 结构化错误态;key 明文不落 DOM", async () => {
    mocks.invoke.mockImplementation(async (_command: string, args: { method: string }) => {
      if (args.method === "secret.set") {
        throw JSON.stringify({ code: "keychain_unavailable", path: "params.name", message: "钥匙链后端不可用" });
      }
      if (args.method === "doctor") return doctorFixture();
      throw JSON.stringify({ code: "method_not_found", path: "method", message: `未知方法 ${args.method}` });
    });
    render(<SettingsScreen />);
    await typeByLabel("model", "glm-4-flash");
    await typeByLabel("LLM API Key", "sk-do-not-echo");
    fireEvent.click(screen.getByRole("button", { name: "保存 LLM 凭据" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("钥匙链后端不可用");
    expect(alert.textContent).toContain("code=keychain_unavailable");
    expect(alert.textContent).toContain("path=params.name");
    expect(screen.queryByTestId("save-status")).toBeNull();
    // 失败时输入保留在用户自己的输入框里(未保存),但绝无旁路回显节点
    expect((screen.getByLabelText("LLM API Key") as HTMLInputElement).value).toBe("sk-do-not-echo");
    expect(document.body.textContent).not.toContain("已写入钥匙链");
  });
});

describe("设置:doctor 验证回显", () => {
  it("挂载即 doctor:凭据存在性 / enrich 现值 / findings 结构化展示", async () => {
    installSidecar(() =>
      doctorFixture({
        plugins: [
          pluginFixture("plugins/stocks.yaml", {
            enrich: enrichFixture("glm-4-flash"),
          }),
        ],
        credentials: [
          credentialFixture(),
          credentialFixture({
            name: "myia/stocks/feishu_chat_id",
            ref: "keychain:myia/stocks/feishu_chat_id",
            exists: false,
          }),
        ],
        findings: [
          {
            severity: "error",
            scope: "credentials",
            code: "keychain_ref_missing",
            message: "钥匙链中不存在凭据 myia/stocks/feishu_chat_id",
          },
        ],
      }),
    );
    render(<SettingsScreen />);

    const panel = await screen.findByTestId("doctor-verify");
    expect(panel.textContent).toContain("myia/stocks/tg_token");
    expect(panel.textContent).toContain("已在钥匙链");
    expect(panel.textContent).toContain("myia/stocks/feishu_chat_id");
    expect(panel.textContent).toContain("缺失(需写入)");
    expect(panel.textContent).toContain("model=glm-4-flash");
    expect(panel.textContent).toContain("keychain_ref_missing");
    expect(panel.textContent).toContain("存在 error 级发现"); // healthy 随 findings 如实降级
  });

  it("保存后自动 doctor 复核:新凭据以 exists=true 回显", async () => {
    const state = installSidecar(() =>
      doctorFixture({
        credentials: [...[...state.secrets.keys()].map((name) => credentialFixture({ name, ref: `keychain:${name}` }))],
      }),
    );
    render(<SettingsScreen />);
    await screen.findByTestId("doctor-verify");

    await typeByLabel("LLM API Key", "sk-later-verify");
    fireEvent.click(screen.getByRole("button", { name: "保存 LLM 凭据" }));

    await waitFor(() => {
      expect(callsOf("doctor").length).toBeGreaterThanOrEqual(2); // 挂载一次 + 保存后一次
    });
    expect(screen.getByTestId("doctor-verify").textContent).toContain("myia/llm/api_key");
    expect(screen.getByTestId("doctor-verify").textContent).toContain("已在钥匙链");
    expect((screen.getByLabelText("LLM API Key") as HTMLInputElement).value).toBe("");
    expect(document.body.textContent).not.toContain("sk-later-verify");
  });
});

describe("设置:推送通道凭据", () => {
  it("按 scope+名字写 myia/<scope>/<name>;值零回显", async () => {
    const state = installSidecar();
    render(<SettingsScreen />);
    await typeByLabel("品类 scope", "stocks");
    await typeByLabel("推送凭据名", "chat_id");
    await typeByLabel("推送凭据值", "oc_abc123private");
    fireEvent.click(screen.getByRole("button", { name: "保存推送凭据" }));

    await screen.findByTestId("save-status");
    expect(callsOf("secret.set")).toContainEqual({ name: "myia/stocks/chat_id", value: "oc_abc123private" });
    expect(document.body.textContent).not.toContain("oc_abc123private");
    expect((screen.getByLabelText("推送凭据值") as HTMLInputElement).value).toBe("");
    expect(state.secrets.get("myia/stocks/chat_id")).toBe("oc_abc123private");
  });

  it("scope 非法 → 前端校验拦截,零协议调用", async () => {
    installSidecar();
    render(<SettingsScreen />);
    await typeByLabel("品类 scope", "Stocks!");
    await typeByLabel("推送凭据值", "whatever");
    fireEvent.click(screen.getByRole("button", { name: "保存推送凭据" }));

    expect(await screen.findByText(/scope 须为品类 id 规则/)).toBeTruthy();
    expect(callsOf("secret.set")).toEqual([]);
  });
});

describe("设置:代理池", () => {
  it("探测走 doctor(--config=路径),逐池回显连通性", async () => {
    const seen: Array<{ config?: string } | undefined> = [];
    installSidecar((params) => {
      seen.push(params);
      return doctorFixture({
        config: params.config ?? null,
        pools: [{ pool: "main", ok: true, latency_seconds: 0.42 }],
      });
    });
    render(<SettingsScreen />);
    await screen.findByTestId("doctor-verify"); // 等挂载 doctor 结算,探测按钮可点
    await typeByLabel("pools YAML 路径", "config/pools.yaml");
    fireEvent.click(screen.getByRole("button", { name: "探测" }));

    await waitFor(() => {
      expect(seen.some((params) => params?.config === "config/pools.yaml")).toBe(true);
    });
    const row = await screen.findByTestId("proxy-pool-row");
    expect(row.textContent).toContain("main");
    expect(row.textContent).toContain("连通");
    expect(row.textContent).toContain("0.42s");
  });

  it("池凭据写入 myia/proxy/<pool>", async () => {
    const state = installSidecar();
    render(<SettingsScreen />);
    await typeByLabel("代理池名", "main");
    await typeByLabel("代理凭据值", "user-ref:pass-ref");
    fireEvent.click(screen.getByRole("button", { name: "保存代理凭据" }));

    await screen.findByTestId("save-status");
    expect(callsOf("secret.set")).toContainEqual({ name: "myia/proxy/main", value: "user-ref:pass-ref" });
    expect(state.secrets.get("myia/proxy/main")).toBe("user-ref:pass-ref");
  });
});
