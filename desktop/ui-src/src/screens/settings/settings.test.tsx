// @vitest-environment jsdom
//
// 设置组件测试:mock sidecar(secret.set / secret.list / doctor 的 JS 假实现),
// 覆盖:LLM 凭据保存(只经 secret.set 入钥匙链、值零回显、保存即清)/
// env: 引用不经界面写 / 表单校验 / secret.set 失败结构化错误 /
// doctor 回显(凭据存在性 + enrich 现值 + findings)/ 推送凭据保存 / 代理池探测。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

const mocks = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
// updater-card 随「更新」分区挂载:两插件模块也须 mock(真实 plugin-updater 会从
// core 导入 Resource/Channel,上面的极简 core mock 不提供;本文件不点更新按钮,
// 行为用例在 updater-card.test.tsx)。
vi.mock("@tauri-apps/plugin-updater", () => ({ check: vi.fn() }));
vi.mock("@tauri-apps/plugin-process", () => ({ relaunch: vi.fn() }));

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
  /** push.test 可编程应答(G5 用例;缺省成功) */
  pushTest: (params: { channel: string; target?: string }) => unknown;
}

function installSidecar(doctorImpl?: (params: { config?: string }) => DoctorResult) {
  const state: SidecarState = {
    secrets: new Map(),
    doctor: doctorImpl ?? (() => doctorFixture()),
    pushTest: (params) => ({ ok: true, channel: params.channel }),
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
        case "secret.delete": {
          const { name } = args.params as { name: string };
          if (!state.secrets.has(name)) {
            throw JSON.stringify({
              code: "secret_not_found",
              path: "params.name",
              message: `系统钥匙链中未找到凭据 ${name},无法删除`,
            });
          }
          state.secrets.delete(name);
          return { name, deleted: true };
        }
        case "push.test":
          return state.pushTest(args.params as { channel: string; target?: string });
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

/** D4 结构重做后设置屏用 useSearchParams(?section= 驱动分区),渲染须包 Router */
function renderScreen(initialEntry = "/settings") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <SettingsScreen />
    </MemoryRouter>,
  );
}

/** 经左侧分区导航切区(拆解表第 1 条:导航驱动右侧子区一屏) */
async function openSection(sectionId: string): Promise<void> {
  fireEvent.click(await screen.findByTestId(`settings-nav-${sectionId}`));
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
    renderScreen();
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
    renderScreen();
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
    renderScreen();
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
    renderScreen();
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
    renderScreen();
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
    renderScreen();

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
    renderScreen();
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
    renderScreen();
    await openSection("push");
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
    renderScreen();
    await openSection("push");
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
    renderScreen();
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
    renderScreen();
    await typeByLabel("代理池名", "main");
    await typeByLabel("代理凭据值", "user-ref:pass-ref");
    fireEvent.click(screen.getByRole("button", { name: "保存代理凭据" }));

    await screen.findByTestId("save-status");
    expect(callsOf("secret.set")).toContainEqual({ name: "myia/proxy/main", value: "user-ref:pass-ref" });
    expect(state.secrets.get("myia/proxy/main")).toBe("user-ref:pass-ref");
  });
});

// ---------------------------------------------------------------------------
// C5(10-03-v112-desktop-parity):钥匙链凭据删除(secret.delete;inline 二次确认)
// ---------------------------------------------------------------------------

describe("设置:凭据删除(C5;D4 后居「高级」分区危险区)", () => {
  it("删除按钮 → 二次确认 → secret.delete → 名单刷新不再列出", async () => {
    const state = installSidecar();
    state.secrets.set("myia/llm/api_key", "v");
    renderScreen();
    await openSection("advanced");

    await screen.findByText("myia/llm/api_key");
    // 第一次点击只亮出确认,不直接删
    fireEvent.click(screen.getByRole("button", { name: "删除凭据 myia/llm/api_key" }));
    expect(callsOf("secret.delete")).toEqual([]);
    fireEvent.click(screen.getByTestId("confirm-delete-myia/llm/api_key"));

    await waitFor(() => expect(callsOf("secret.delete")).toContainEqual({ name: "myia/llm/api_key" }));
    await waitFor(() => expect(screen.queryByText("myia/llm/api_key")).toBeNull());
    expect(state.secrets.has("myia/llm/api_key")).toBe(false);
  });

  it("取消确认零删除;删除失败(secret_not_found)结构化上屏", async () => {
    const state = installSidecar();
    state.secrets.set("myia/push/token", "v");
    renderScreen();
    await openSection("advanced");

    await screen.findByText("myia/push/token");
    fireEvent.click(screen.getByRole("button", { name: "删除凭据 myia/push/token" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(callsOf("secret.delete")).toEqual([]);
    expect(screen.getByText("myia/push/token")).toBeTruthy(); // 名单未动

    // 人为制造不一致:名单显示但钥匙链已无此名 → 第二次删除报 secret_not_found
    state.secrets.delete("myia/push/token");
    fireEvent.click(screen.getByRole("button", { name: "删除凭据 myia/push/token" }));
    fireEvent.click(screen.getByTestId("confirm-delete-myia/push/token"));
    const box = await screen.findByRole("alert");
    expect(box.textContent).toContain("secret_not_found");
  });
});

// ---------------------------------------------------------------------------
// B3+C11(10-03-v112-desktop-parity):评分与反馈分区(enrich.enabled/model 写回
// yaml.read→文本手术→yaml.save(mtime 锁)→doctor 复核)+ mutateEnrichField 纯函数
// ---------------------------------------------------------------------------

const SAMPLE_YAML = `id: stocks
name: 量化快讯
enrich:
  enabled: true   # 精评开关(B3)
  model: glm-4-flash
  budget_per_run: 40
push:
  - channel: feishu_card
    enabled: true
`;

describe("mutateEnrichField 纯函数(文本手术,注释保真)", () => {
  it("改 enabled:只动命中行值段,行内注释与其余原文逐字节不动", async () => {
    const { mutateEnrichField } = await import("./api");
    const next = mutateEnrichField(SAMPLE_YAML, "enabled", "false");
    expect(next).toContain("  enabled: false   # 精评开关(B3)");
    expect(next).toContain("  model: glm-4-flash");
    expect(next).toContain("    enabled: true"); // push 节内同名键不受扰(只在 enrich 块内匹配)
    // 其余行原样(行数不变,除命中行外逐行相等)
    expect(next.split("\n")).toHaveLength(SAMPLE_YAML.split("\n").length);
  });

  it("改 model:同款手术", async () => {
    const { mutateEnrichField } = await import("./api");
    const next = mutateEnrichField(SAMPLE_YAML, "model", "glm-4.6");
    expect(next).toContain("  model: glm-4.6");
    expect(next).toContain("  enabled: true   # 精评开关(B3)");
  });

  it("无顶层 enrich 节 / 节内无该字段行 → CategoryNodeError(enrich_node_missing)", async () => {
    const { mutateEnrichField, CategoryNodeError } = await import("./api");
    expect(() => mutateEnrichField("id: stocks\n", "enabled", "false")).toThrow(CategoryNodeError);
    expect(() =>
      mutateEnrichField("enrich:\n  model: glm-4-flash\n", "budget_per_run", "10"),
    ).toThrow(/budget_per_run/);
  });
});

describe("设置:评分与反馈分区(B3+C11)", () => {
  function installYamlSidecar(options?: { saveImpl?: (params: { file: string; content: string }) => void }) {
    let mtime = 1_700_000_000;
    const state = {
      saved: null as { file: string; content: string; expected_mtime: number | null } | null,
      secrets: new Map<string, string>(),
      doctorImpl: () =>
        doctorFixture({
          plugins: [pluginFixture("plugins/stocks.yaml", { enrich: enrichFixture("glm-4-flash") })],
        }),
    };
    mocks.invoke.mockImplementation(
      async (_command: string, args: { method: string; params?: unknown }) => {
        switch (args.method) {
          case "doctor":
            return state.doctorImpl();
          case "secret.list":
            return { names: [...state.secrets.keys()].sort() };
          case "yaml.read":
            return { file: "plugins/stocks.yaml", content: SAMPLE_YAML, size: SAMPLE_YAML.length, mtime };
          case "yaml.save": {
            const params = args.params as { file: string; content: string; expected_mtime: number | null };
            if (params.expected_mtime !== mtime) {
              throw JSON.stringify({
                code: "mtime_conflict",
                path: "params.expected_mtime",
                message: "文件已被其他编辑改写,请刷新后重试",
              });
            }
            options?.saveImpl?.(params);
            state.saved = params;
            mtime += 1;
            return {
              file: params.file,
              written: true,
              created: false,
              backed_up: "plugins/stocks.yaml.bak",
              mtime,
              warnings: [],
            };
          }
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

  it("挂载即渲染逐品类行(doctor enrich 节);budget 只读护栏明示", async () => {
    installYamlSidecar();
    renderScreen();
    const row = await screen.findByTestId("enrich-row-plugins/stocks.yaml");
    expect(row.textContent).toContain("stocks.yaml");
    expect(row.textContent).toContain("budget_per_run = 40");
    expect(row.textContent).toContain("精评已启用");
  });

  it("停用开关(Switch 即时写回):yaml.read → 文本手术(enabled: true→false)→ yaml.save(mtime 锁)→ doctor 复核", async () => {
    const state = installYamlSidecar();
    renderScreen();
    const toggle = await screen.findByRole("switch", { name: "精评开关 plugins/stocks.yaml" });
    fireEvent.click(toggle);

    await waitFor(() => expect(state.saved).not.toBeNull());
    expect(state.saved?.file).toBe("plugins/stocks.yaml");
    expect(state.saved?.content).toContain("  enabled: false   # 精评开关(B3)");
    // enrich 节内 enabled 不再为 true(整行匹配;push 节内的同名键不受扰)
    expect(state.saved?.content).not.toMatch(/^  enabled: true/m);
    expect(state.saved?.expected_mtime).toBe(1_700_000_000);
    // 写后 doctor 复核(callsOf 不适用此 mock,断言 invoke 序列含 doctor after save)
    await waitFor(() => {
      const methods = mocks.invoke.mock.calls.map(([, args]) => (args as { method: string }).method);
      const saveAt = methods.lastIndexOf("yaml.save");
      expect(methods.slice(saveAt)).toContain("doctor");
    });
  });

  it("model 写回:与现值一致禁用保存;改值后 yaml.save 带 mtime 锁", async () => {
    const state = installYamlSidecar();
    renderScreen();
    const input = await screen.findByLabelText("精评模型 plugins/stocks.yaml");
    // 现值一致 → 保存禁用(本项目无 jest-dom,原生 disabled 直查)
    expect((screen.getByRole("button", { name: /保存 model/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(input, { target: { value: "glm-4.6" } });
    const save = screen.getByRole("button", { name: /保存 model/ }) as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    fireEvent.click(save);

    await waitFor(() => expect(state.saved?.content).toContain("  model: glm-4.6"));
    expect(state.saved?.content).toMatch(/^  enabled: true\s+#/m); // enrich 节 enabled 未被扰
  });

  it("mtime_conflict 结构化透传(mtime 乐观锁);不静默吞", async () => {
    let first = true;
    installYamlSidecar({
      saveImpl: () => {
        if (first) {
          first = false;
          throw JSON.stringify({
            code: "mtime_conflict",
            path: "params.expected_mtime",
            message: "文件已被其他编辑改写,请刷新后重试",
          });
        }
      },
    });
    renderScreen();
    fireEvent.click(await screen.findByRole("switch", { name: "精评开关 plugins/stocks.yaml" }));
    // 断言限定本卡(结构重做后 vision-form 仅在「视觉」分区挂载,本区唯一 alert)
    const card = screen.getByTestId("enrich-feedback-card");
    const box = await within(card).findByRole("alert");
    expect(box.textContent).toContain("mtime_conflict");
    expect(box.textContent).toContain("已被其他编辑改写");
  });

  it("品类无 enrich 节:CategoryNodeError 行内指引(去配置编辑),不弹协议错误", async () => {
    const noEnrichYaml = "id: stocks\nname: 量化快讯\n";
    mocks.invoke.mockImplementation(
      async (_command: string, args: { method: string }) => {
        if (args.method === "doctor") {
          return doctorFixture({
            plugins: [pluginFixture("plugins/stocks.yaml", { enrich: enrichFixture("glm-4-flash") })],
          });
        }
        if (args.method === "secret.list") return { names: [] };
        if (args.method === "yaml.read") {
          return { file: "plugins/stocks.yaml", content: noEnrichYaml, size: 10, mtime: 1 };
        }
        if (args.method === "yaml.save") {
          return {
            file: "plugins/stocks.yaml",
            written: true,
            created: false,
            backed_up: null,
            mtime: 2,
            warnings: [],
          };
        }
        throw JSON.stringify({ code: "method_not_found", path: "method", message: "x" });
      },
    );
    renderScreen();
    fireEvent.click(await screen.findByRole("switch", { name: "精评开关 plugins/stocks.yaml" }));
    const note = await screen.findByTestId("enrich-node-error");
    expect(note.textContent).toContain("缺少 enrich");
    expect(note.textContent).toContain("配置编辑");
  });

  it("push 声明指引链接(在「推送」分区卡底)→ 配置编辑屏路由(HashRouter 锚点)", async () => {
    installYamlSidecar();
    renderScreen();
    await openSection("push");
    const link = await screen.findByRole("link", { name: "去配置编辑改 push 声明" });
    expect(link.getAttribute("href")).toBe("#/yaml-editor");
  });
});

// ---------------------------------------------------------------------------
// 推送测试按钮(G5 前半,10-03-feed-ux):push.test 真发一条,行内回显
// ---------------------------------------------------------------------------

describe("设置:推送测试(G5)", () => {
  it("scope 已填 → target 组 keychain 引用下发;成功回显 ok 徽标", async () => {
    installSidecar();
    renderScreen();
    await openSection("push");

    await typeByLabel("品类 scope", "stocks");
    fireEvent.click(screen.getByRole("button", { name: "发送测试" }));

    await waitFor(() => expect(callsOf("push.test")).toHaveLength(1));
    // channel 取表单当前选中(feishu_card);target = 表单 scope/凭据名组合
    expect(callsOf("push.test")).toContainEqual({
      channel: "feishu_card",
      target: "keychain:myia/stocks/chat_id",
    });
    expect(await screen.findByText("通道连通")).toBeTruthy();
    expect(screen.getByTestId("push-test-result").textContent).toContain("feishu_card");
    // 状态行绝不出现凭据值(这里本就没填值;引用名不是秘密)
    expect(document.body.textContent).not.toContain("keychain:myia/stocks/chat_id");
  });

  it("scope 空 → 不带 target(走通道默认 env 引用链,如实测)", async () => {
    installSidecar();
    renderScreen();
    await openSection("push");

    fireEvent.click(screen.getByRole("button", { name: "发送测试" }));
    await waitFor(() => expect(callsOf("push.test")).toHaveLength(1));
    expect(callsOf("push.test")).toContainEqual({ channel: "feishu_card" });
  });

  it("失败结构化透传:env_var_missing → 通道失败徽标 + code:message 行内回显", async () => {
    const state = installSidecar();
    state.pushTest = () => {
      throw JSON.stringify({
        code: "env_var_missing",
        path: "params.channel",
        message: "环境变量 FEISHU_BOT_TOKEN 未设置",
      });
    };
    renderScreen();
    await openSection("push");

    fireEvent.click(screen.getByRole("button", { name: "发送测试" }));
    expect(await screen.findByText("通道失败")).toBeTruthy();
    const note = screen.getByTestId("push-test-result");
    expect(note.textContent).toContain("env_var_missing");
    expect(note.textContent).toContain("FEISHU_BOT_TOKEN");
  });
});

// ---------------------------------------------------------------------------
// D4 结构重做(10-03-ui-deep-imitation,对照 teardown-linear-settings 第 1/2/5/6
// 条):左分区导航(当前项高亮)+ 右侧每子区一屏(区标题+描述+卡片列)+ URL
// ?section= 驱动深链 + 危险区 Destructive Card 隔离。
// ---------------------------------------------------------------------------

describe("设置:分区导航与危险区(D4 结构重做)", () => {
  it("五分区导航齐(通用/视觉/推送/更新/高级);缺省进通用,通用卡直见而他区卡不挂载", async () => {
    installSidecar();
    renderScreen();

    for (const id of ["general", "vision", "push", "update", "advanced"]) {
      expect(screen.getByTestId(`settings-nav-${id}`)).toBeTruthy();
    }
    // 当前项高亮:aria-current 打在通用上
    expect(screen.getByTestId("settings-nav-general").getAttribute("aria-current")).toBe("true");
    expect(screen.getByTestId("settings-nav-vision").getAttribute("aria-current")).toBeNull();
    // 每子区一屏:通用区可见(LLM 表单),看图/更新卡未挂载
    expect(screen.getByTestId("settings-section-general")).toBeTruthy();
    expect(screen.getByLabelText("base_url")).toBeTruthy();
    expect(screen.queryByLabelText("本地 base_url")).toBeNull(); // VisionForm 未挂载
    expect(screen.queryByTestId("updater-status")).toBeNull(); // UpdaterCard 未挂载(其 idle 态无 status 节点,双保险)
    expect(screen.queryByTestId("settings-danger-zone")).toBeNull(); // 危险区不在通用
  });

  it("点导航切区:右列换屏 + aria-current 随迁;视觉区挂 VisionForm 两卡所需表单", async () => {
    mocks.invoke.mockImplementation(async (_command: string, args: { method: string }) => {
      if (args.method === "secret.list") return { names: [] };
      if (args.method === "doctor") return doctorFixture();
      if (args.method === "image.config.read") {
        return {
          file: "/home/vision.yaml",
          exists: true,
          config: {
            channel_default: "local",
            local: { base_url: "http://127.0.0.1:8080/v1", model: "" },
            cloud: { base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4.6v", api_key: null },
            ocr: { enabled: true, engine_default: "vision" },
          },
        };
      }
      throw JSON.stringify({ code: "method_not_found", path: "method", message: `未知方法 ${args.method}` });
    });
    renderScreen();
    await screen.findByTestId("doctor-verify");

    await openSection("vision");
    expect(screen.getByTestId("settings-section-vision")).toBeTruthy();
    expect(screen.queryByTestId("settings-section-general")).toBeNull(); // 每子区一屏:通用卸载
    expect(screen.getByTestId("settings-nav-vision").getAttribute("aria-current")).toBe("true");
    await waitFor(() => {
      expect((screen.getByLabelText("本地 base_url") as HTMLInputElement).value).toBe("http://127.0.0.1:8080/v1");
    });
    // 区标题+描述在位(拆解表第 2 条)
    expect(screen.getByRole("heading", { level: 2, name: "视觉" })).toBeTruthy();
  });

  it("URL ?section= 深链:直进高级区,危险区 Destructive 卡直见且为该区末位卡", async () => {
    const state = installSidecar();
    state.secrets.set("myia/llm/api_key", "v");
    renderScreen("/settings?section=advanced");

    const danger = await screen.findByTestId("settings-danger-zone");
    expect(danger.textContent).toContain("危险区");
    expect(danger.textContent).toContain("二次确认");
    expect(danger.textContent).toContain("myia/llm/api_key");
    // 危险区在高级区底部:其后仅安全底线文案,无其他设置卡(section 内最后一个 Card)
    const section = screen.getByTestId("settings-section-advanced");
    const cards = section.querySelectorAll("[data-slot='card']");
    expect(cards[cards.length - 1]).toBe(danger);
    // 非法 section 值回落通用
  });

  it("非法 ?section= 值回落通用分区(不白屏)", async () => {
    installSidecar();
    renderScreen("/settings?section=nonsense");
    expect(screen.getByTestId("settings-section-general")).toBeTruthy();
    expect(screen.getByLabelText("base_url")).toBeTruthy();
  });

  it("每区保存态反馈在卡片底栏:保存中禁用按钮,成功后 save-status 留在本卡", async () => {
    let releaseSave: (() => void) | null = null;
    mocks.invoke.mockImplementation(async (_command: string, args: { method: string; params?: unknown }) => {
      if (args.method === "doctor") return doctorFixture();
      if (args.method === "secret.list") return { names: [] };
      if (args.method === "secret.set") {
        await new Promise<void>((resolve) => {
          releaseSave = resolve; // 挂起保存,冻结「保存中」态供断言
        });
        const { name, value } = args.params as SecretSetParams;
        return { name, stored: value.length > 0 };
      }
      throw JSON.stringify({ code: "method_not_found", path: "method", message: "x" });
    });
    renderScreen();
    await typeByLabel("base_url", "https://api.example.com");
    const save = screen.getByRole("button", { name: "保存 LLM 凭据" }) as HTMLButtonElement;
    fireEvent.click(save);
    // 保存中:按钮禁用 + 行内保存中文案(role=status)
    await screen.findByText("保存中…");
    expect((screen.getByRole("button", { name: "保存 LLM 凭据" }) as HTMLButtonElement).disabled).toBe(true);
    (releaseSave as (() => void) | null)?.();
    const status = await screen.findByTestId("save-status");
    expect(status.textContent).toContain("myia/llm/base_url");
    expect(status.closest("[data-slot='card']")?.textContent).toContain("LLM 精评"); // 反馈留在本卡底栏
  });
});
