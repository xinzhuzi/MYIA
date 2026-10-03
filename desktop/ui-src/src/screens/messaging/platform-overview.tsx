/**
 * 平台总览(task 10-03-messaging-hermes-look R2/R3):消息屏最上区,布局照上游
 * Hermes 蓝本(~/.hermes/hermes-agent/apps/desktop/src/app/messaging/index.tsx)
 * 的「左列平台卡 + 右栏详情面板」(MasterDetail)结构重排 —— MYIA 栈内贴近,
 * 不逐字拷贝 TSX:
 *
 * - 左列:平台卡网格(头像 + 名称 + 状态点;R1 头像芯片见 platform-icons)。
 *   点击卡选中,右栏切换内容;窄屏(<lg)折叠为上下布局。
 * - 右栏:详情面板 = 平台描述 / 三态状态说明(证据来源)/ 出站凭据指南
 *   (platforms 任务 R4 内容移入此处,左卡不再内嵌展开)/ 已连接时的目录
 *   条目速览(只读;改名/别名编辑仍在下方「通道目录」)。
 * - 三态色彩(R3,走 MYIA 语义 tokens,不硬编码色值):已连接=绿(--ok)、
 *   需要设置=黄(--warning)、即将支持=灰(--muted/--muted-foreground);
 *   状态点(StateDotTone)+ 状态胶囊(StatePill)+ 卡片描边共用同一套
 *   tone 映射,筛选 tabs 激活态与对应 tone 呼应。
 * - 筛选(全部/已连接/未启用)沿用;切筛选时若当前选中平台不再匹配,选中
 *   栏自动切入该筛选下第一张卡(上游 handleStatusFilter 同交互流)。
 * - 三态派生纯函数(目录桶 + secret.list 名单 → 状态)零协议往返,见下方。
 */
import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { isDeadEntry, type ChannelEntry } from "./api";
import { PlatformAvatar } from "./platform-icons";

// ---------------------------------------------------------------------------
// 平台清单(前端常量;数据源:父任务 10-03-hermes-messaging PRD W1/W2/W3 波次表)
// ---------------------------------------------------------------------------

/** 一条出站凭据 key 的说明(与 run 时实际读取的环境变量名一一对应)。 */
export interface PlatformGuideKey {
  /** 凭据 key(环境变量名;协议面只出名,值永不出)。 */
  key: string;
  /** 用途,直白中文一句话。 */
  purpose: string;
}

/** 指南一步:纯文本,或「文字 + 命令块」(curl 等可复制命令)。 */
export type PlatformGuideStep = string | { text: string; code: string };

/** 已实装平台的出站凭据指南(R4;唯一入口在右栏详情面板)。 */
export interface PlatformGuide {
  keys: PlatformGuideKey[];
  steps: PlatformGuideStep[];
}

/** 已实装平台(W1;目录/发送链路已入库,状态真实派生)。 */
export interface ImplementedPlatform {
  id: string;
  name: string;
  /** 详情面板头部的一行直白描述(只陈述已实装的事实,不预告功能)。 */
  description: string;
  guide: PlatformGuide;
}

/** 未实装平台(W2/W3 波次;灰卡「即将支持」)。 */
export interface UpcomingPlatform {
  id: string;
  name: string;
  wave: "W2" | "W3";
}

export const IMPLEMENTED_PLATFORMS: readonly ImplementedPlatform[] = [
  {
    id: "feishu",
    name: "飞书",
    description: "飞书开放平台机器人(W1 已实装):tenant_access_token 出站卡片发送 + 群目录发现;凭据经环境变量注入 run。",
    guide: {
      keys: [
        {
          key: "FEISHU_BOT_TOKEN",
          purpose: "tenant_access_token(应用级令牌);出站卡片发送与群目录发现共用,run 时读环境变量",
        },
      ],
      steps: [
        "打开飞书开放平台(open.feishu.cn)→ 开发者后台 → 「创建企业自建应用」,记下应用的 App ID 与 App Secret。",
        "在应用里添加「机器人」能力;「权限管理」开通 im:message:send_as_bot(以机器人身份发消息)和 im:chat:readonly(读机器人所在群列表,目录发现用)。",
        "「版本管理与发布」发布版本并让组织管理员审核通过;把机器人拉进要推送的群。",
        {
          text: "手工换 tenant token:在终端执行下面这条(把 cli_xxx / xxx 换成你的 App ID / App Secret),应答里的 tenant_access_token 字段就是令牌;细节以开放平台文档「获取 tenant_access_token 内部接口」页为准。",
          code: 'curl -X POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal \\\n  -H "Content-Type: application/json" \\\n  -d \'{"app_id":"cli_xxx","app_secret":"xxx"}\'',
        },
        "把令牌写入环境变量 FEISHU_BOT_TOKEN(例如在 ~/.zshrc 加一行 export FEISHU_BOT_TOKEN=t-xxx,重开终端/应用后生效)。注意令牌有效期约 2 小时,过期后按上一步重换并更新环境变量。",
        "回到本屏点飞书分组里的「刷新」列出群目录,再到下区「推送规则」勾选推送对象。",
      ],
    },
  },
  {
    id: "telegram",
    name: "Telegram",
    description: "Telegram Bot API(W1 已实装):BotFather 令牌出站发送 + 会话随真实 bot 流量被动积累入目录。",
    guide: {
      keys: [
        {
          key: "TELEGRAM_BOT_TOKEN",
          purpose: "机器人令牌;出站发送与被动目录积累共用,run 时读环境变量",
        },
        {
          key: "TELEGRAM_CHAT_ID",
          purpose: "缺省推送会话的数字 chat_id;只在规则没写 targets 的旧式用法里生效",
        },
      ],
      steps: [
        "在 Telegram 里找 @BotFather 发送 /newbot,按提示给机器人起显示名和用户名(用户名须以 bot 结尾);BotFather 最后回复的 HTTP API token 就是 TELEGRAM_BOT_TOKEN。",
        "拿 chat_id:找 @userinfobot 发任意消息,它回复里的 Id 就是你自己的数字 chat_id(与机器人的私聊会话就用它)。",
        "群聊不必手抄 id:把机器人拉进群、在群里发条消息,本屏目录会直接记下该群;私聊目录同理——不给机器人发消息,目录里就不会有会话。",
        "把令牌与 chat_id 写入环境变量 TELEGRAM_BOT_TOKEN 与 TELEGRAM_CHAT_ID(例如在 ~/.zshrc 里 export,重开终端/应用后生效)。",
        "给机器人发条消息后回本屏刷新:目录出现该会话,即可在「推送规则」里勾选它。",
      ],
    },
  },
];

/** W2/W3 未实装平台(父任务 PRD 波次表登记锚点;灰卡,零交互)。 */
export const UPCOMING_PLATFORMS: readonly UpcomingPlatform[] = [
  { id: "weixin", name: "微信", wave: "W2" },
  { id: "wecom", name: "企业微信", wave: "W2" },
  { id: "dingtalk", name: "钉钉", wave: "W2" },
  { id: "ntfy", name: "ntfy", wave: "W2" },
  { id: "slack", name: "Slack", wave: "W3" },
  { id: "discord", name: "Discord", wave: "W3" },
  { id: "whatsapp_cloud", name: "WhatsApp", wave: "W3" },
  { id: "signal", name: "Signal", wave: "W3" },
  { id: "line", name: "LINE", wave: "W3" },
  { id: "matrix", name: "Matrix", wave: "W3" },
  { id: "mattermost", name: "Mattermost", wave: "W3" },
  { id: "google_chat", name: "Google Chat", wave: "W3" },
  { id: "teams", name: "Microsoft Teams", wave: "W3" },
  { id: "email", name: "邮件", wave: "W3" },
  { id: "sms", name: "短信", wave: "W3" },
  { id: "irc", name: "IRC", wave: "W3" },
  { id: "simplex", name: "SimpleX", wave: "W3" },
  { id: "bluebubbles", name: "BlueBubbles", wave: "W3" },
  { id: "qqbot", name: "QQ 机器人", wave: "W3" },
  { id: "yuanbao", name: "元宝", wave: "W3" },
  { id: "a2a", name: "A2A", wave: "W3" },
  { id: "photon", name: "Photon", wave: "W3" },
  { id: "homeassistant", name: "Home Assistant", wave: "W3" },
  { id: "msgraph_webhook", name: "MS Graph Webhook", wave: "W3" },
  { id: "buzz", name: "Buzz", wave: "W3" },
  { id: "raft", name: "Raft", wave: "W3" },
];

/** 未实装平台的详情描述(按波次;不虚构平台功能,只说排期与实装后的去处)。 */
const UPCOMING_DESCRIPTION: Record<"W2" | "W3", string> = {
  W2: "尚未实装;已排入 W2(近期)波次。实装后此处将给出出站凭据指南与目录速览。",
  W3: "尚未实装;在 W3(远期)波次排期中。实装后此处将给出出站凭据指南与目录速览。",
};

// ---------------------------------------------------------------------------
// 三态派生(纯函数,零协议往返、零后端概念)
// ---------------------------------------------------------------------------

/** 平台卡三态:已连接 / 需要设置 / 即将支持。 */
export type PlatformCardStatus = "connected" | "needs_setup" | "coming_soon";

/** 筛选档(R2):全部 / 已连接 / 未启用。 */
export type PlatformFilter = "all" | "connected" | "disabled";

/** 一张平台卡的视图模型(左网格渲染、筛选与详情面板的最小完整单元)。 */
export interface PlatformCard {
  id: string;
  name: string;
  wave: "W1" | "W2" | "W3";
  status: PlatformCardStatus;
  /** 目录条数(仅已实装平台有;灰卡恒 0)。 */
  directoryCount: number;
  /** 钥匙链探测命中的凭据名(状态说明里展示「已录几项」;空 = 无钥匙链证据)。 */
  matchedSecretNames: string[];
  guide: PlatformGuide | null;
  /** 详情面板头部的一行描述(已实装 = 事实描述;未实装 = 波次排期说明)。 */
  description: string;
}

/**
 * 钥匙链名单里挑出该平台的凭据名(探测口径,secret.list 返回名)。
 *
 * 命中两种形态之一:`myia/<platform>/<name>`(scope=平台 id)或任意
 * scope 下叶子名等于该平台凭据 key(如 `myia/push/FEISHU_BOT_TOKEN`)。
 */
export function matchSecretNames(
  platformId: string,
  credentialKeys: readonly string[],
  allNames: readonly string[],
): string[] {
  return allNames.filter((name) => {
    if (!name.startsWith("myia/")) return false;
    const rest = name.slice("myia/".length);
    const slash = rest.indexOf("/");
    if (slash <= 0) return false;
    const scope = rest.slice(0, slash);
    const leaf = rest.slice(slash + 1);
    return scope === platformId || credentialKeys.includes(leaf);
  });
}

/**
 * 已实装平台 → 已连接 / 需要设置(纯前端派生)。
 *
 * 已连接 = 凭据可解析(钥匙链探测命中)**或** 目录桶非空 —— 目录条目只能
 * 经一次成功的发现(feishu)或真实 bot 流量(telegram 被动积累)进入,
 * 非空即「凭据当时可用且刷新成功」的前端可见证据(env: 令牌不在钥匙链,
 * 这条信号覆盖它)。两条证据都没有 = 凭据缺失 → 需要设置。
 */
export function deriveImplementedStatus(
  bucket: readonly ChannelEntry[],
  matchedSecretNames: readonly string[],
): "connected" | "needs_setup" {
  const credentialsFound = matchedSecretNames.length > 0;
  const refreshSucceeded = bucket.length > 0;
  return credentialsFound || refreshSucceeded ? "connected" : "needs_setup";
}

/** 目录桶 + 钥匙链名单 → 全量平台卡(已实装在前,波次序在后)。 */
export function buildPlatformCards(
  directory: Record<string, readonly ChannelEntry[]>,
  secretNames: readonly string[],
): PlatformCard[] {
  return [
    ...IMPLEMENTED_PLATFORMS.map((platform) => {
      const bucket = directory[platform.id] ?? [];
      const matched = matchSecretNames(
        platform.id,
        platform.guide.keys.map((entry) => entry.key),
        secretNames,
      );
      return {
        id: platform.id,
        name: platform.name,
        wave: "W1" as const,
        status: deriveImplementedStatus(bucket, matched),
        directoryCount: bucket.length,
        matchedSecretNames: matched,
        guide: platform.guide,
        description: platform.description,
      };
    }),
    ...UPCOMING_PLATFORMS.map((platform) => ({
      id: platform.id,
      name: platform.name,
      wave: platform.wave,
      status: "coming_soon" as const,
      directoryCount: 0,
      matchedSecretNames: [],
      guide: null,
      description: UPCOMING_DESCRIPTION[platform.wave],
    })),
  ];
}

/** 筛选档判定:未启用 = 需要设置(已实装凭据缺失)+ 即将支持(未实装)。 */
export function matchesFilter(card: PlatformCard, filter: PlatformFilter): boolean {
  if (filter === "all") return true;
  if (filter === "connected") return card.status === "connected";
  return card.status !== "connected";
}

// ---------------------------------------------------------------------------
// 展示常量(三态文案与 tone 映射;文案直白中文,tone 走 MYIA 语义 tokens)
// ---------------------------------------------------------------------------

const STATUS_LABEL: Record<PlatformCardStatus, string> = {
  connected: "已连接",
  needs_setup: "需要设置",
  coming_soon: "即将支持",
};

/** 状态点 tone(R3):绿 --ok / 黄 --warning / 灰 --muted-foreground,全走语义 token。 */
const STATUS_DOT_TONE: Record<PlatformCardStatus, string> = {
  connected: "bg-ok",
  needs_setup: "bg-warning",
  coming_soon: "bg-muted-foreground/50",
};

/** 状态胶囊 tone(R3):与 Badge 语义色变体同源(border/bg/text 三段全 token)。 */
const STATE_PILL_TONE: Record<PlatformCardStatus, string> = {
  connected: "border-ok/30 bg-ok/10 text-ok",
  needs_setup: "border-warning/30 bg-warning/10 text-warning",
  coming_soon: "border-border bg-muted/50 text-muted-foreground",
};

/** 左卡描边 tone(R3):已连接绿框 / 需要设置黄框 / 即将支持中性灰框。 */
const CARD_BORDER_TONE: Record<PlatformCardStatus, string> = {
  connected: "border-ok/30",
  needs_setup: "border-warning/30",
  coming_soon: "border-border/60",
};

/** 筛选 tab 激活态 tone(R3):全部=品牌青 / 已连接=绿 / 未启用=黄(可行动子集)。 */
const FILTER_TONE_CLASS: Record<PlatformFilter, string> = {
  all: "border-primary/40 bg-primary/10 text-primary",
  connected: "border-ok/40 bg-ok/10 text-ok",
  disabled: "border-warning/40 bg-warning/10 text-warning",
};

const FILTER_LABEL: Record<PlatformFilter, string> = {
  all: "全部",
  connected: "已连接",
  disabled: "未启用",
};

const FILTER_ORDER: readonly PlatformFilter[] = ["all", "connected", "disabled"];

// ---------------------------------------------------------------------------
// 组件
// ---------------------------------------------------------------------------

interface PlatformOverviewProps {
  /** 与消息屏同款加载态;error 时本区不渲染(ErrorBox 已在屏顶如实报错)。 */
  status: "loading" | "error" | "ready";
  /** channels.list 的 platforms 视图(平台 → 目录桶;详情面板目录速览用)。 */
  directory: Record<string, ChannelEntry[]>;
  /** secret.list 名单(凭据探测;钥匙链不可用时为空 = 降级无证据)。 */
  secretNames: string[];
  /** 死信键清单(`platform:chat_id`;目录速览的死信徽标用)。 */
  dead: string[];
}

/**
 * 平台总览区:筛选 tabs(与状态 tone 呼应)+ 左平台卡网格 / 右详情面板
 * 双栏(R2;窄屏折叠上下布局)。凭据指南唯一入口在右栏详情面板。
 */
export function PlatformOverview({ dead, directory, secretNames, status }: PlatformOverviewProps) {
  const [filter, setFilter] = useState<PlatformFilter>("all");
  // 缺省选中第一张已实装卡(上游 platformIds[0] 同缺省);详情面板随之就位
  const [selectedId, setSelectedId] = useState<string>(IMPLEMENTED_PLATFORMS[0]?.id ?? "");

  const cards = useMemo(() => buildPlatformCards(directory, secretNames), [directory, secretNames]);
  const counts = useMemo(() => {
    const connected = cards.filter((card) => card.status === "connected").length;
    return { all: cards.length, connected, disabled: cards.length - connected };
  }, [cards]);
  const visible = useMemo(() => cards.filter((card) => matchesFilter(card, filter)), [cards, filter]);
  // 选中卡从全量卡里找(非 visible):平台状态自行变化离开筛选时,详情栏
  // 保持打开 —— 只有「点击筛选 tab」这一个动作会带动选中切换(上游同交互流)。
  const selected = useMemo(
    () => cards.find((card) => card.id === selectedId) ?? cards[0] ?? null,
    [cards, selectedId],
  );

  function handleFilter(next: PlatformFilter) {
    setFilter(next);
    // 切筛选后若当前选中平台不再匹配,选中栏切入该筛选下第一张卡
    if (selected && !matchesFilter(selected, next)) {
      const first = cards.find((card) => matchesFilter(card, next));
      if (first) {
        setSelectedId(first.id);
      }
    }
  }

  if (status === "error") return null;

  return (
    <div className="px-6" data-testid="platform-overview">
      <Card>
        <CardContent className="flex flex-col gap-3 p-4">
          <p className="flex items-center gap-1.5 text-sm font-medium text-foreground">
            平台总览
            <span className="text-xs font-normal text-muted-foreground">
              (左列点选平台,右栏看详情:状态说明 / 出站凭据指南 / 目录速览;灰卡平台按波次排期,尚未实装)
            </span>
          </p>

          {status === "loading" ? (
            <div className="flex flex-col gap-2" aria-label="加载中">
              {[0, 1, 2].map((index) => (
                <Skeleton key={index} className="h-9 w-full" />
              ))}
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="平台筛选">
                {FILTER_ORDER.map((id) => (
                  <Button
                    key={id}
                    size="sm"
                    variant="outline"
                    className={cn(
                      "rounded-full",
                      filter === id ? FILTER_TONE_CLASS[id] : "text-muted-foreground",
                    )}
                    aria-pressed={filter === id}
                    data-testid={`platform-filter-${id}`}
                    onClick={() => handleFilter(id)}
                  >
                    {FILTER_LABEL[id]}({counts[id]})
                  </Button>
                ))}
              </div>

              {/* R2 双栏:左平台卡网格 + 右详情面板;窄屏(<lg)折叠上下布局 */}
              <div className="grid grid-cols-1 items-start gap-3 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
                <ul
                  className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-1.5"
                  data-testid="platform-card-list"
                  aria-label="平台卡列表"
                >
                  {visible.map((card) => (
                    <li key={card.id}>
                      <PlatformCardButton
                        card={card}
                        selected={selected?.id === card.id}
                        onSelect={() => setSelectedId(card.id)}
                      />
                    </li>
                  ))}
                  {visible.length === 0 ? (
                    <li className="col-span-full text-xs text-muted-foreground">该筛选下暂无平台。</li>
                  ) : null}
                </ul>

                <div
                  className="min-w-0 rounded-md border border-border bg-muted/20 p-4"
                  data-testid="platform-detail"
                  aria-label="平台详情面板"
                >
                  {selected ? (
                    <PlatformDetailPanel card={selected} dead={dead} directory={directory} />
                  ) : (
                    <p className="text-xs text-muted-foreground">暂无平台。</p>
                  )}
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/** 左列平台卡(R2):头像 + 名称 + 状态点;点击选中(右栏切换详情)。 */
function PlatformCardButton({
  card,
  selected,
  onSelect,
}: {
  card: PlatformCard;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      className={cn(
        "flex w-full items-center gap-2 rounded-md border p-2 text-left transition-colors hover:bg-accent/60 hover:text-accent-foreground",
        card.status === "coming_soon" && "opacity-70",
        CARD_BORDER_TONE[card.status],
        // 选中态:品牌青环 + 浅底,叠加在三态描边之上(状态色不被选中色吃掉)
        selected && "bg-primary/10 ring-1 ring-primary/40",
      )}
      data-testid={`platform-card-${card.id}`}
      onClick={onSelect}
    >
      <PlatformAvatar platformId={card.id} platformName={card.name} />
      <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">{card.name}</span>
      <StatusDotTone status={card.status} />
    </button>
  );
}

/** 状态点(照上游 StatusDot 画法:1.5px 圆点,tone 全走语义 token)。 */
function StatusDotTone({ status }: { status: PlatformCardStatus }) {
  return (
    <span aria-hidden="true" className={cn("inline-block size-1.5 shrink-0 rounded-full", STATUS_DOT_TONE[status])} />
  );
}

/** 详情栏小节标题(照上游 SectionTitle 节奏:小号大写间距,中文取 tracking-wide)。 */
function SectionTitle({ children }: { children: ReactNode }) {
  return <h4 className="text-[11px] font-semibold tracking-wide text-muted-foreground">{children}</h4>;
}

/** 状态说明文案(三态证据来源;连接态列命中信号,缺配置态给下一步动作)。 */
function statusExplanation(card: PlatformCard): string {
  if (card.status === "coming_soon") {
    return `${card.wave} 波次排期平台,尚未实装:无凭据可言,目录恒空;实装节奏见接入路线图。`;
  }
  if (card.status === "connected") {
    const signals = [
      card.matchedSecretNames.length > 0 ? `钥匙链命中 ${card.matchedSecretNames.length} 项凭据名` : null,
      card.directoryCount > 0 ? `目录非空(${card.directoryCount} 个会话)` : null,
    ]
      .filter(Boolean)
      .join("、");
    return `已连接:${signals || "信号已就绪"}。可直接在下方「推送规则」勾选该平台目录里的会话为推送对象。`;
  }
  return "需要设置:钥匙链未探测到该平台凭据名,且目录为空(无一次成功发现或真实 bot 流量的证据)。按下方「出站凭据指南」录入凭据,再到下方「通道目录」点该平台的「刷新」验证。";
}

/** 右栏详情面板(R2):描述 / 状态说明 / 凭据指南 / 已连接时的目录速览。 */
function PlatformDetailPanel({
  card,
  dead,
  directory,
}: {
  card: PlatformCard;
  dead: string[];
  directory: Record<string, ChannelEntry[]>;
}) {
  const bucket = card.status === "coming_soon" ? [] : (directory[card.id] ?? []);
  return (
    <div className="flex flex-col gap-4">
      {/* 头(照上游 PlatformDetail header:头像 + 名称 + 状态胶囊 + 描述) */}
      <header className="flex items-start gap-3">
        <PlatformAvatar platformId={card.id} platformName={card.name} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="min-w-0 truncate text-[0.9375rem] font-semibold tracking-tight text-foreground">
              {card.name}
            </h3>
            <StatePill status={card.status}>{STATUS_LABEL[card.status]}</StatePill>
          </div>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{card.description}</p>
          <p className="truncate font-mono text-[11px] text-muted-foreground">{card.id}</p>
        </div>
      </header>

      <section>
        <SectionTitle>状态说明</SectionTitle>
        <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{statusExplanation(card)}</p>
      </section>

      {card.guide ? (
        <section className="flex flex-col gap-2">
          <SectionTitle>出站凭据指南</SectionTitle>
          <PlatformGuideView guide={card.guide} platformId={card.id} />
        </section>
      ) : null}

      {card.status === "connected" ? (
        <section className="flex flex-col gap-2">
          <SectionTitle>目录速览</SectionTitle>
          <DirectoryQuickView bucket={bucket} dead={dead} platformId={card.id} />
        </section>
      ) : null}
    </div>
  );
}

/** 状态胶囊(照上游 StatePill 画法:胶囊 + 内嵌状态点 + 文案,tone 走语义 token)。 */
function StatePill({ children, status }: { children: string; status: PlatformCardStatus }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        STATE_PILL_TONE[status],
      )}
    >
      <StatusDotTone status={status} />
      {children}
    </span>
  );
}

/** 目录速览(R2;已连接时):只读条目列表;改名/别名/死信处理仍在下方「通道目录」。 */
function DirectoryQuickView({
  bucket,
  dead,
  platformId,
}: {
  bucket: ChannelEntry[];
  dead: string[];
  platformId: string;
}) {
  if (bucket.length === 0) {
    return (
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        目录为空:到下方「通道目录」点 {platformId} 组的「刷新」(飞书主动发现)或等会话被动进入(telegram 随
        bot 流量积累)。
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-[11px] text-muted-foreground">
        {bucket.length} 个会话;只读速览,改名 / 别名 / 死信处理在下方「通道目录」。
      </p>
      <ul className="flex flex-col gap-1">
        {bucket.map((entry) => (
          <li
            key={entry.chat_id}
            className="flex items-center justify-between gap-2 rounded-md border border-border/50 px-2.5 py-1.5"
          >
            <span className="flex min-w-0 items-center gap-1.5 text-xs">
              <Badge variant="outline">{entry.type}</Badge>
              <span className="truncate font-medium text-foreground">{entry.name}</span>
              {isDeadEntry(entry, dead) ? <Badge variant="destructive">死信</Badge> : null}
            </span>
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{entry.chat_id}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 出站凭据指南面板(R4 内容移入详情栏):凭据 key 用途 + 获取步骤(中文直白)。 */
function PlatformGuideView({ guide, platformId }: { guide: PlatformGuide; platformId: string }) {
  return (
    <div
      className="flex flex-col gap-2 rounded-md bg-muted/30 p-2.5"
      data-testid={`platform-guide-${platformId}`}
    >
      <p className="text-xs font-medium text-foreground">定向推送只需要这些;凭据只覆盖出站,入站项零出现。</p>
      <div className="flex flex-col gap-1">
        {guide.keys.map((entry) => (
          <p key={entry.key} className="text-[11px] leading-relaxed text-muted-foreground">
            <span className="font-mono text-foreground">{entry.key}</span> — {entry.purpose}
          </p>
        ))}
      </div>
      <ol className="flex list-decimal flex-col gap-1.5 pl-4 text-[11px] leading-relaxed text-muted-foreground">
        {guide.steps.map((step, index) => (
          <li key={index}>
            {typeof step === "string" ? (
              step
            ) : (
              <>
                {step.text}
                <code className="mt-1 block overflow-x-auto rounded bg-background px-2 py-1 font-mono text-[10px] whitespace-pre-wrap text-foreground">
                  {step.code}
                </code>
              </>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
