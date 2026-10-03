// @vitest-environment jsdom
//
// 消息屏组件测试:mock sidecar(壳命令 sidecar_request 的 JS 假实现,
// 内存态模拟 channel_directory/aliases/ledger/rules),覆盖:目录渲染
// (平台分组/类型徽标/死信徽标/别名徽标)、别名行内编辑(调用 channels.alias
// set/delete)、targets 多选产出 platform:名称 spec 与 push.write 全量保存、
// 平台刷新按钮、空态(先配平台凭据指引)、断连态(结构化错误 + 重试)。
// 平台总览(10-03-messaging-hermes-look):左平台卡网格 + 右详情面板双栏 ——
// 三态(已连接绿/需要设置黄/即将支持灰,secret.list 凭据探测 + channels.list
// 目录信号派生)、全部/已连接/未启用筛选(tone 呼应 + 切筛选带动选中)、
// 28 平台头像芯片(见 platform-icons.test.tsx)、详情面板(描述/状态说明/
// 出站凭据指南唯一入口/目录速览)、底部状态条(sidecar 健康 + 已连接平台
// 计数)、入站项零出现(扫码/允许的用户 ID/webhook secret 不渲染)。
// 协议契约权威:desktop/entry.py `_m_channels_*` / `_m_push_write` +
// 任务 10-03-messaging-ui design.md §D2。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

const mocks = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));

import { MessagingScreen } from "./messaging-screen";
import type { ChannelsView } from "./api";
import {
  buildPlatformCards,
  deriveImplementedStatus,
  matchSecretNames,
  matchesFilter,
} from "./platform-overview";

const FILE = "plugins/messaging-demo.yaml";

// ---------------------------------------------------------------------------
// 协议夹具(形状逐字段对照 ./api.ts / desktop/entry.py 应答载荷)
// ---------------------------------------------------------------------------

function channelEntry(
  platform: string,
  chatId: string,
  name: string,
  extra: Partial<{ type: string; last_seen: number | null }> = {},
) {
  return {
    platform,
    chat_id: chatId,
    name,
    type: extra.type ?? "group",
    thread_id: null,
    last_seen: extra.last_seen ?? null,
  };
}

function channelsViewFixture(): ChannelsView {
  return {
    data_root: "/tmp/home",
    updated_at: "2026-10-03T08:00:00",
    platforms: {
      feishu: [
        channelEntry("feishu", "oc_1", "AI中转站合伙人群", { last_seen: 1760000000 }),
        channelEntry("feishu", "oc_2", "羊毛反馈群", { type: "dm" }),
      ],
      telegram: [channelEntry("telegram", "12345", "测试私聊", { type: "dm" })],
    },
    aliases: {} as Record<string, Record<string, string>>,
    dead: ["feishu:oc_2"],
    rules: [
      {
        file: FILE,
        category_id: "messaging-demo",
        category_name: "消息屏夹具",
        parse_ok: true,
        error: null,
        entries: [
          {
            index: 0,
            channel: "feishu_card",
            platform: "feishu",
            targets: ["feishu:AI中转站合伙人群"],
            has_template: false,
            route_count: 1,
            raw: {
              channel: "feishu_card",
              targets: ["feishu:AI中转站合伙人群"],
              route: [{ when: "category == 'freebie'", mode: "immediate" }],
            },
          },
          {
            index: 1,
            channel: "stdout",
            platform: null,
            targets: [],
            has_template: false,
            route_count: 0,
            raw: { channel: "stdout" },
          },
        ],
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// mock sidecar:内存态 = channels.list 四视图;写方法只记账(不真改状态)
// ---------------------------------------------------------------------------

type Handler = (params: never) => unknown;
type SidecarMap = Record<string, Handler>;

function okSidecar() {
  const view = channelsViewFixture();
  // secret.list 名单(平台总览凭据探测用;只有名字,值永不可读)
  const secrets = { names: [] as string[] };
  // health 是否应答失败(false = R4 状态条「sidecar 不可达」路径;缺省 true)
  const health = { ok: true };
  const calls: { method: string; params: unknown }[] = [];
  const map: SidecarMap = {
    "channels.list": () => JSON.parse(JSON.stringify(view)) as unknown,
    "channels.refresh": (params: never) => {
      const { platform } = params as { platform: string };
      const bucket = view.platforms[platform] ?? [];
      return { platform, merged: bucket.length, entries: bucket };
    },
    "channels.alias": (params: never) => {
      const { platform, chat_id, name } = params as {
        platform: string;
        chat_id: string;
        name: string | null;
      };
      if (name === null) {
        delete view.aliases[platform]?.[chat_id];
      } else {
        (view.aliases[platform] ??= {})[chat_id] = name;
      }
      return { platform, chat_id: chat_id, deleted: name === null, name };
    },
    "push.write": (params: never) => {
      const { file, push } = params as { file: string; push: unknown[] };
      return { file, written: true as const, changed: true, push };
    },
    "secret.list": () => ({ names: [...secrets.names] }),
    // health 应答形状只需支撑「一来一回成功 = 存活」判定(R4 状态条)
    health: () => {
      if (!health.ok) {
        throw JSON.stringify({
          code: "sidecar_not_running",
          path: "$",
          message: "sidecar 进程未运行",
        });
      }
      return { healthy: true };
    },
  };
  const record = (method: string, params: unknown) => {
    calls.push({ method, params });
  };
  return { map, view, secrets, health, calls, record };
}

/** 安装 mock sidecar:所有 invoke("sidecar_request") 走此分派;未知方法=结构化 404 */
function installSidecar(map: SidecarMap, record?: (method: string, params: unknown) => void): void {
  mocks.invoke.mockImplementation(
    async (_command: string, args: { method: string; params?: unknown }) => {
      const handler = map[args.method];
      if (record) record(args.method, args.params);
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

function lastParams(calls: { method: string; params: unknown }[], method: string): unknown {
  return calls.filter((call) => call.method === method).at(-1)?.params;
}

beforeEach(() => {
  mocks.invoke.mockReset();
});
afterEach(() => {
  cleanup(); // vitest 非 globals 模式下 RTL 不自动清理,防 DOM 跨测试污染
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------

describe("消息:通道目录渲染", () => {
  it("平台分组渲染:名称/类型徽标/最后发现/死信徽标;死信只标在对应行", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );

    const feishu = within(await screen.findByTestId("platform-feishu"));
    expect(feishu.getByText("AI中转站合伙人群")).toBeTruthy();
    // 类型徽标:group(群)/dm(私聊)如实呈现
    expect(feishu.getByText("group")).toBeTruthy();
    expect(feishu.getByText("dm")).toBeTruthy();
    // 死信徽标只在 oc_2 行;别名徽标此时不存在
    const deadRow = feishu.getByTestId("entry-oc_2");
    expect(within(deadRow).getByText("死信")).toBeTruthy();
    expect(within(feishu.getByTestId("entry-oc_1")).queryByText("死信")).toBeNull();
    expect(screen.queryByText("别名")).toBeNull();
    // telegram 平台分组同样渲染
    expect(within(screen.getByTestId("platform-telegram")).getByText("测试私聊")).toBeTruthy();
  });
});

describe("消息:别名行内编辑", () => {
  it("改名:输入新名保存 → channels.alias(set)参数正确,成功提示呈现", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const feishu = within(await screen.findByTestId("platform-feishu"));
    fireEvent.click(within(feishu.getByTestId("entry-oc_1")).getByRole("button", { name: "改名" }));

    const input = feishu.getByLabelText("别名 oc_1") as HTMLInputElement;
    expect(input.value).toBe("AI中转站合伙人群"); // 预填当前名
    fireEvent.change(input, { target: { value: "重点群" } });
    fireEvent.click(feishu.getByRole("button", { name: "保存" }));

    expect(lastParams(sidecar.calls, "channels.alias")).toEqual({
      platform: "feishu",
      chat_id: "oc_1",
      name: "重点群",
    });
    await waitFor(() => {
      expect(screen.getByTestId("messaging-notice").textContent).toContain("已命名 feishu:重点群");
    });
  });

  it("已命名条目出「别名」徽标与「取消别名」;点击 → channels.alias(name:null)", async () => {
    const sidecar = okSidecar();
    sidecar.view.aliases = { feishu: { oc_1: "手工名" } };
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const feishu = within(await screen.findByTestId("platform-feishu"));
    const row = feishu.getByTestId("entry-oc_1");
    expect(within(row).getByText("别名")).toBeTruthy();

    fireEvent.click(within(row).getByRole("button", { name: "取消别名" }));
    expect(lastParams(sidecar.calls, "channels.alias")).toEqual({
      platform: "feishu",
      chat_id: "oc_1",
      name: null,
    });
    await waitFor(() => {
      expect(screen.getByTestId("messaging-notice").textContent).toContain("已取消");
    });
  });
});

describe("消息:targets 多选与 push.write 保存", () => {
  it("勾选产出 platform:名称 spec;保存提交完整 push 数组(仅 targets 变)", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );

    const group = await screen.findByTestId("rule-file-messaging-demo");
    // 初始态:已选 AI中转站合伙人群(spec 文本直接可见)
    expect(group.textContent).toContain("feishu:AI中转站合伙人群");

    // 取消 AI中转站合伙人群,勾上 羊毛反馈群(spec = feishu:羊毛反馈群)
    fireEvent.click(within(group).getByLabelText("对象 AI中转站合伙人群"));
    fireEvent.click(within(group).getByLabelText("对象 羊毛反馈群"));

    fireEvent.click(within(group).getByRole("button", { name: "保存推送对象" }));

    expect(lastParams(sidecar.calls, "push.write")).toEqual({
      file: FILE,
      push: [
        {
          channel: "feishu_card",
          targets: ["feishu:羊毛反馈群"],
          route: [{ when: "category == 'freebie'", mode: "immediate" }],
        },
        { channel: "stdout" },
      ],
    });
    await waitFor(() => {
      expect(screen.getByTestId("messaging-notice").textContent).toContain("已保存");
    });
  });

  it("不支持寻址的条目(stdout/webhook)不出选择器,如实说明", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const group = await screen.findByTestId("rule-file-messaging-demo");
    expect(group.textContent).toContain("该通道不支持目录寻址");
  });
});

describe("消息:平台刷新", () => {
  it("点平台组内「刷新」→ channels.refresh({platform});失败 toast 结构化错误", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    fireEvent.click(
      within(await screen.findByTestId("platform-feishu")).getByRole("button", { name: "刷新" }),
    );
    expect(lastParams(sidecar.calls, "channels.refresh")).toEqual({ platform: "feishu" });
    await waitFor(() => {
      expect(screen.getByTestId("messaging-notice").textContent).toContain("feishu 目录已刷新");
    });

    // 失败路径:凭据缺失族错误如实带回,目录不清空
    sidecar.map["channels.refresh"] = () => {
      throw JSON.stringify({
        code: "channel_refresh_failed",
        path: "params.platform",
        message: "feishu 目录发现失败,旧目录不动: [credential_not_found] 飞书 bot 凭据未配置",
        data: { platform: "feishu", code: "credential_not_found" },
      });
    };
    fireEvent.click(
      within(screen.getByTestId("platform-feishu")).getByRole("button", { name: "刷新" }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("messaging-notice").textContent).toContain("credential_not_found");
    });
    // 旧目录仍在(作用域钉在目录区,规则区的同名选项不参与匹配)
    expect(within(screen.getByTestId("platform-feishu")).getByText("AI中转站合伙人群")).toBeTruthy();
  });
});

describe("消息:空态与断连态", () => {
  it("目录为空 → 「先配平台凭据」指引空态", async () => {
    const sidecar = okSidecar();
    sidecar.view.platforms = {};
    sidecar.view.rules = [];
    installSidecar(sidecar.map);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    expect(await screen.findByText("通道目录还是空的")).toBeTruthy();
    expect(screen.getByText(/先到「设置」录入平台凭据/)).toBeTruthy();
    expect(screen.getByText(/还没有品类 YAML/)).toBeTruthy();
    // 状态条:目录空 = 已连接 0(28 张在册卡),sidecar 本身存活
    const bar = await screen.findByTestId("messaging-statusbar");
    await waitFor(() => {
      expect(bar.textContent).toContain("sidecar 正常");
    });
    expect(bar.textContent).toContain("已连接平台 0/28");
  });

  it("sidecar 不可达 → 结构化错误(code/path)+ 重试;状态条同步转红", async () => {
    mocks.invoke.mockImplementation(async () => {
      throw JSON.stringify({ code: "sidecar_not_running", path: "$", message: "sidecar 进程未运行" });
    });
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("sidecar 进程未运行");
    expect(alert.textContent).toContain("code=sidecar_not_running");
    expect(alert.textContent).toContain("path=$");
    // R4 状态条如实反映 sidecar 不可达(channels.list/health 全失败)
    await waitFor(() => {
      expect(screen.getByTestId("messaging-statusbar").textContent).toContain("sidecar 不可达");
    });
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => {
      expect(mocks.invoke.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
  });
});

// ---------------------------------------------------------------------------
// 平台总览(10-03-messaging-hermes-look R1-R4:左网格 + 右详情面板)
// ---------------------------------------------------------------------------

describe("消息:平台总览三态与头像卡", () => {
  it("左卡网格 28 张全带头像;缺省选中 feishu,详情面板出已连接 + 目录速览(死信徽标)", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );

    const overview = await screen.findByTestId("platform-overview");
    const list = within(overview).getByTestId("platform-card-list");
    // R1 验收:28 张平台卡全部带头像芯片(2 精确标 + 26 通用标)
    expect(within(list).getAllByRole("listitem")).toHaveLength(28);
    expect(list.querySelectorAll("[data-testid^='platform-avatar-']")).toHaveLength(28);
    expect(within(list).getByTestId("platform-avatar-feishu")).toBeTruthy();
    expect(within(list).getByTestId("platform-avatar-telegram")).toBeTruthy();

    // 缺省选中第一张已实装卡:详情面板已连接 + 目录速览只读镜像
    const detail = within(overview).getByTestId("platform-detail");
    expect(within(detail).getByText("已连接")).toBeTruthy();
    expect(detail.textContent).toContain("目录非空(2 个会话)");
    expect(within(detail).getByText("AI中转站合伙人群")).toBeTruthy();
    expect(within(detail).getByText("羊毛反馈群")).toBeTruthy();
    // 死信徽标在详情速览的 oc_2 行上;速览标注只读(编辑入口仍在下方通道目录)
    expect(detail.textContent).toContain("oc_2");
    expect(within(detail).getAllByText("死信")).toHaveLength(1);
    expect(detail.textContent).toContain("只读速览");
  });

  it("点击 telegram 卡 → 详情切换(Telegram 头 + 目录速览 1 个会话);左卡网格不动", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const overview = await screen.findByTestId("platform-overview");
    fireEvent.click(within(overview).getByTestId("platform-card-telegram"));

    const detail = within(overview).getByTestId("platform-detail");
    expect(within(detail).getByText("Telegram")).toBeTruthy();
    expect(within(detail).getByText("已连接")).toBeTruthy();
    expect(within(detail).getByText("测试私聊")).toBeTruthy();
    expect(within(detail).queryByText("AI中转站合伙人群")).toBeNull();
    // 卡网格仍完整(选中只影响右栏)
    expect(within(overview).getByTestId("platform-card-list").querySelectorAll("[data-testid^='platform-avatar-']")).toHaveLength(28);
  });

  it("凭据缺失(目录空且钥匙链无命中)→ 详情面板需要设置(说明含下一步动作)", async () => {
    const sidecar = okSidecar();
    sidecar.view.platforms = {};
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const overview = await screen.findByTestId("platform-overview");
    const detail = within(overview).getByTestId("platform-detail");
    expect(within(detail).getByText("需要设置")).toBeTruthy();
    expect(detail.textContent).toContain("目录为空");
    expect(detail.textContent).toContain("出站凭据指南"); // 需要设置时指南仍在(唯一入口)
  });

  it("钥匙链探测命中(secret.list:scope 或叶子名)→ 目录空也判已连接", async () => {
    const sidecar = okSidecar();
    sidecar.view.platforms = {};
    sidecar.secrets.names = [
      "myia/feishu/bot_token", // scope = 平台 id 命中
      "myia/push/TELEGRAM_BOT_TOKEN", // 叶子名 = 凭据 key 命中
      "myia/llm/api_key", // 无关凭据,不参与
    ];
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const overview = await screen.findByTestId("platform-overview");
    // 缺省选中 feishu → 详情栏说明钥匙链命中;切 telegram 同样命中
    const detail = within(overview).getByTestId("platform-detail");
    expect(within(detail).getByText("已连接")).toBeTruthy();
    expect(detail.textContent).toContain("钥匙链命中 1 项凭据名");
    fireEvent.click(within(overview).getByTestId("platform-card-telegram"));
    expect(within(overview).getByTestId("platform-detail").textContent).toContain(
      "钥匙链命中 1 项凭据名",
    );
  });

  it("灰卡(weixin)点击选中 → 详情即将支持 + W2 排期说明;无凭据指南、无目录速览", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const overview = await screen.findByTestId("platform-overview");
    fireEvent.click(within(overview).getByTestId("platform-card-weixin"));

    const detail = within(overview).getByTestId("platform-detail");
    expect(within(detail).getByText("微信")).toBeTruthy();
    expect(within(detail).getByText("即将支持")).toBeTruthy();
    expect(detail.textContent).toContain("W2");
    expect(detail.textContent).toContain("尚未实装");
    expect(within(detail).queryByTestId("platform-guide-weixin")).toBeNull();
    expect(within(detail).queryByText("目录速览")).toBeNull();
  });
});

describe("消息:平台总览筛选 tabs", () => {
  it("全部(28)/已连接/未启用 正确分组;tab 文案带计数;已连接档隐藏灰卡且选中不动", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const overview = await screen.findByTestId("platform-overview");

    // 缺省 = 全部:已实装 2 + 未实装 26 = 28 张卡
    expect(within(overview).getByTestId("platform-filter-all").textContent).toBe("全部(28)");
    expect(within(overview).getByTestId("platform-filter-connected").textContent).toBe("已连接(2)");
    expect(within(overview).getByTestId("platform-filter-disabled").textContent).toBe("未启用(26)");
    expect(within(overview).getByTestId("platform-card-weixin")).toBeTruthy();

    // 已连接:只剩已实装且已连接的卡;选中(feishu)仍匹配筛选 → 详情栏不动
    fireEvent.click(within(overview).getByTestId("platform-filter-connected"));
    expect(within(overview).getByTestId("platform-card-feishu")).toBeTruthy();
    expect(within(overview).queryByTestId("platform-card-weixin")).toBeNull();
    expect(within(overview).getByTestId("platform-detail").textContent).toContain("飞书");

    // 未启用:需要设置 + 即将支持(此处 fixture 全已连接 → 只剩灰卡)
    fireEvent.click(within(overview).getByTestId("platform-filter-disabled"));
    expect(within(overview).queryByTestId("platform-card-feishu")).toBeNull();
    expect(within(overview).queryByTestId("platform-card-telegram")).toBeNull();
    expect(within(overview).getByTestId("platform-card-weixin")).toBeTruthy();
    expect(within(overview).getByTestId("platform-card-homeassistant")).toBeTruthy();
  });

  it("切「未启用」带动选中(上游交互流):选中平台不再匹配 → 详情栏切入该筛选下第一张卡", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const overview = await screen.findByTestId("platform-overview");
    // 缺省选中 feishu(已连接);切到「未启用」后选中不再匹配 → 自动选中该筛选
    // 下第一张卡。fixture 只有 feishu/telegram 已连接 → 第一张未启用 = W2 转实装
    // 但凭据缺失的 ntfy(需要设置,不是灰卡)。
    fireEvent.click(within(overview).getByTestId("platform-filter-disabled"));
    const detail = within(overview).getByTestId("platform-detail");
    // 名称与 id 行都写着 ntfy(h3 + font-mono id),用文本包含断言选中对象
    expect(detail.textContent).toContain("ntfy");
    expect(within(detail).getByText("需要设置")).toBeTruthy();
    expect(detail.textContent).toContain("无自动发现"); // manual 平台的需要设置说明
  });

  it("已实装但凭据缺失 → 归入「未启用」而非「已连接」;切档后详情栏保持在 feishu(需要设置)", async () => {
    const sidecar = okSidecar();
    sidecar.view.platforms = {};
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const overview = await screen.findByTestId("platform-overview");
    expect(within(overview).getByTestId("platform-filter-connected").textContent).toBe("已连接(0)");
    expect(within(overview).getByTestId("platform-filter-disabled").textContent).toBe("未启用(28)");
    fireEvent.click(within(overview).getByTestId("platform-filter-disabled"));
    expect(within(overview).getByTestId("platform-card-feishu")).toBeTruthy();
    const detail = within(overview).getByTestId("platform-detail");
    expect(within(detail).getByText("飞书")).toBeTruthy();
    expect(within(detail).getByText("需要设置")).toBeTruthy();
  });
});

describe("消息:详情栏出站凭据指南(唯一入口)", () => {
  it("选中 feishu 即见指南(零点击,卡片上无展开按钮):tenant token 手工换 + curl 命令", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const overview = await screen.findByTestId("platform-overview");
    // 缺省选中 feishu:指南直接在详情栏,不再需要「点开卡片」
    const guide = within(overview).getByTestId("platform-guide-feishu");
    expect(guide.textContent).toContain("FEISHU_BOT_TOKEN");
    expect(guide.textContent).toContain("tenant_access_token");
    expect(guide.textContent).toContain("im:message:send_as_bot");
    expect(guide.textContent).toContain(
      "curl -X POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    );
    // R2:左卡上不再有指南展开交互(无 aria-expanded 的按钮)
    const feishuCard = within(overview).getByTestId("platform-card-feishu");
    expect(feishuCard.getAttribute("aria-expanded")).toBeNull();
    expect(feishuCard.tagName).toBe("BUTTON"); // 卡即选中按钮,无二级展开
  });

  it("点击 telegram 卡 → 指南切到 telegram(@BotFather + @userinfobot + 两个凭据 key)", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const overview = await screen.findByTestId("platform-overview");
    fireEvent.click(within(overview).getByTestId("platform-card-telegram"));

    const guide = within(overview).getByTestId("platform-guide-telegram");
    expect(guide.textContent).toContain("TELEGRAM_BOT_TOKEN");
    expect(guide.textContent).toContain("TELEGRAM_CHAT_ID");
    expect(guide.textContent).toContain("@BotFather");
    expect(guide.textContent).toContain("@userinfobot");
    // 详情栏一次只展示一个平台:feishu 指南随选中切换离开
    expect(within(overview).queryByTestId("platform-guide-feishu")).toBeNull();
  });

  it("入站项零出现:详情栏先后选中 feishu/telegram,两份整屏文本不渲染扫码/允许的用户 ID/webhook secret", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const overview = await screen.findByTestId("platform-overview");
    // 缺省选中 feishu → 指南已在;再切 telegram,两份选中态的整屏文本都收进来断言
    await within(overview).findByTestId("platform-guide-feishu");
    const feishuOpenText = document.body.textContent ?? "";

    fireEvent.click(within(overview).getByTestId("platform-card-telegram"));
    await within(overview).findByTestId("platform-guide-telegram");
    const telegramOpenText = document.body.textContent ?? "";

    for (const text of [feishuOpenText, telegramOpenText]) {
      expect(text).not.toMatch(/扫码/);
      expect(text).not.toMatch(/允许的用户/);
      expect(text).not.toMatch(/webhook\s*secret/i);
    }
  });

  it("W2 三平台指南各就位(ntfy 自建/公共 topic、钉钉群设置→自定义机器人、企微管理后台自建应用)", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const overview = await screen.findByTestId("platform-overview");

    // ntfy:凭据 key + curl 冒烟 + 直达寻址指引;指南文案只覆盖出站
    fireEvent.click(within(overview).getByTestId("platform-card-ntfy"));
    const ntfyGuide = within(overview).getByTestId("platform-guide-ntfy");
    expect(ntfyGuide.textContent).toContain("NTFY_TARGET");
    expect(ntfyGuide.textContent).toContain("NTFY_TOKEN");
    expect(ntfyGuide.textContent).toContain("docs.ntfy.sh");
    expect(ntfyGuide.textContent).toContain("curl -d");
    expect(ntfyGuide.textContent).toContain("ntfy:my-games-alerts");
    expect(ntfyGuide.textContent).toContain("无自动发现");

    // 钉钉:群设置 → 机器人 → 自定义机器人;加签密钥可配;直达 = 完整 webhook
    fireEvent.click(within(overview).getByTestId("platform-card-dingtalk"));
    const dingGuide = within(overview).getByTestId("platform-guide-dingtalk");
    expect(dingGuide.textContent).toContain("DINGTALK_WEBHOOK_URL");
    expect(dingGuide.textContent).toContain("群设置");
    expect(dingGuide.textContent).toContain("自定义");
    expect(dingGuide.textContent).toContain("dingtalk_secret");
    expect(dingGuide.textContent).toContain("oapi.dingtalk.com/robot/send");

    // 企微:管理后台建自建应用 → corpid/secret/agentid 三凭据 + touser
    fireEvent.click(within(overview).getByTestId("platform-card-wecom"));
    const wecomGuide = within(overview).getByTestId("platform-guide-wecom");
    expect(wecomGuide.textContent).toContain("WECOM_CORPID");
    expect(wecomGuide.textContent).toContain("WECOM_CORPSECRET");
    expect(wecomGuide.textContent).toContain("WECOM_AGENTID");
    expect(wecomGuide.textContent).toContain("work.weixin.qq.com");
    expect(wecomGuide.textContent).toContain("wecom:ZhangSan");
    // 群聊/markdown 是蓝本外能力:描述如实声明只做 text 私聊
    expect(within(overview).getByTestId("platform-detail").textContent).toContain("text 私聊");
  });
});

describe("消息:底部状态条(R4)", () => {
  it("sidecar 健康(health 一来一回成功)+ 已连接平台计数(已连接/在册总数)", async () => {
    const sidecar = okSidecar();
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    const bar = await screen.findByTestId("messaging-statusbar");
    await waitFor(() => {
      expect(bar.textContent).toContain("sidecar 正常");
    });
    expect(bar.textContent).toContain("已连接平台 2/28");
  });

  it("health 失败但目录视图可用 → 状态条如实转「sidecar 不可达」,计数照常派生", async () => {
    const sidecar = okSidecar();
    sidecar.health.ok = false;
    installSidecar(sidecar.map, sidecar.record);
    render(
      <MemoryRouter>
        <MessagingScreen />
      </MemoryRouter>,
    );
    // 目录照常(channels.list 成功);状态条只反映 health 信号
    expect(await screen.findByTestId("platform-feishu")).toBeTruthy();
    const bar = screen.getByTestId("messaging-statusbar");
    await waitFor(() => {
      expect(bar.textContent).toContain("sidecar 不可达");
    });
    expect(bar.textContent).toContain("已连接平台 2/28");
  });
});

describe("消息:平台总览派生纯函数", () => {
  it("matchSecretNames:scope=平台 id 或叶子名=凭据 key 命中;其余忽略", () => {
    const names = [
      "myia/feishu/bot_token",
      "myia/push/FEISHU_BOT_TOKEN",
      "myia/llm/api_key",
      "feishu/裸名",
      "myia/feishu", // 无 name 段
    ];
    expect(matchSecretNames("feishu", ["FEISHU_BOT_TOKEN"], names)).toEqual([
      "myia/feishu/bot_token",
      "myia/push/FEISHU_BOT_TOKEN",
    ]);
    expect(matchSecretNames("telegram", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"], names)).toEqual([]);
  });

  it("deriveImplementedStatus:目录非空或钥匙链命中 = 已连接;双缺 = 需要设置", () => {
    const bucket = [channelEntry("feishu", "oc_1", "群")];
    expect(deriveImplementedStatus(bucket, [])).toBe("connected");
    expect(deriveImplementedStatus([], ["myia/feishu/bot_token"])).toBe("connected");
    expect(deriveImplementedStatus([], [])).toBe("needs_setup");
  });

  it("buildPlatformCards:5 已实装(全缺凭据=需要设置)+ 23 未实装;灰卡无指南、id 唯一", () => {
    const cards = buildPlatformCards({}, []);
    expect(cards).toHaveLength(28);
    expect(cards.filter((card) => card.status === "needs_setup")).toHaveLength(5);
    expect(cards.filter((card) => card.status === "coming_soon")).toHaveLength(23);
    expect(new Set(cards.map((card) => card.id)).size).toBe(28);
    // W2 三平台已转实装:有指南、discovery=manual(无自动发现,蓝本事实)
    for (const id of ["ntfy", "dingtalk", "wecom"]) {
      const card = cards.find((c) => c.id === id)!;
      expect(card.guide).not.toBeNull();
      expect(card.wave).toBe("W2");
      expect(card.discovery).toBe("manual");
    }
    expect(cards.find((c) => c.id === "feishu")!.discovery).toBe("auto");
    expect(cards.find((c) => c.id === "telegram")!.discovery).toBe("passive");
    for (const card of cards.filter((c) => c.status === "coming_soon")) {
      expect(card.guide).toBeNull();
      expect(card.directoryCount).toBe(0);
    }
  });

  it("matchesFilter:未启用 = 需要设置 + 即将支持;已连接只收 connected", () => {
    const cards = buildPlatformCards(
      { feishu: [channelEntry("feishu", "oc_1", "群")] },
      ["myia/telegram/bot_token"],
    );
    const byId = Object.fromEntries(cards.map((card) => [card.id, card]));
    expect(byId.feishu.status).toBe("connected"); // 目录非空
    expect(byId.telegram.status).toBe("connected"); // 钥匙链命中
    expect(matchesFilter(byId.feishu, "connected")).toBe(true);
    expect(matchesFilter(byId.weixin, "connected")).toBe(false);
    expect(matchesFilter(byId.feishu, "disabled")).toBe(false);
    expect(matchesFilter(byId.weixin, "disabled")).toBe(true); // 即将支持 → 未启用
    const emptyCards = buildPlatformCards({}, []);
    const needsSetup = emptyCards.find((card) => card.status === "needs_setup")!;
    expect(needsSetup).toBeTruthy();
    expect(matchesFilter(needsSetup, "disabled")).toBe(true); // 需要设置 → 未启用
  });
});
