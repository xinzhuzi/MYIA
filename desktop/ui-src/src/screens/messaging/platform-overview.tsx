/**
 * 平台总览(task 10-03-messaging-platforms R1-R4):消息屏最上区的平台卡片网格。
 *
 * - R1 网格:已实装平台(feishu/telegram,W1)带真实状态与目录条数;W2/W3
 *   未实装平台渲染「即将支持」灰卡(清单硬编码自父任务
 *   10-03-hermes-messaging PRD 波次表,不做后端注册表)。
 * - R2 筛选:全部 / 已连接 / 未启用 三档;「未启用」= 已实装但凭据缺失
 *   (需要设置)+ 未实装平台(即将支持)。
 * - R3 三态徽标(纯前端派生,零后端概念):已连接(绿)= 凭据可解析或
 *   目录非空;需要设置(黄)= 凭据缺失且目录为空;即将支持(灰)= W2/W3。
 *   凭据探测 = 现有 secret.list 名单(钥匙链命名空间 myia/<platform>/<name>
 *   或叶子名等于该平台凭据 key;env: 令牌对协议面不可见,由目录信号兜底);
 *   刷新状态 = channels.list 返回的平台目录桶(条目只能经一次成功发现或
 *   真实 bot 流量进入,非空即刷新成功的可见证据)。
 * - R4 凭据指南:点开已实装平台卡片展开出站凭据获取步骤(中文直白,
 *   本地常量,不引外链依赖);只覆盖定向出站所需,入站项零出现。
 */
import { LayoutGrid } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import type { ChannelEntry } from "./api";

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

/** 已实装平台的出站凭据指南(R4;点开卡片展开)。 */
export interface PlatformGuide {
  keys: PlatformGuideKey[];
  steps: PlatformGuideStep[];
}

/** 已实装平台(W1;目录/发送链路已入库,状态真实派生)。 */
export interface ImplementedPlatform {
  id: string;
  name: string;
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

// ---------------------------------------------------------------------------
// 三态派生(R3;纯函数,零协议往返、零后端概念)
// ---------------------------------------------------------------------------

/** 平台卡三态:已连接 / 需要设置 / 即将支持。 */
export type PlatformCardStatus = "connected" | "needs_setup" | "coming_soon";

/** 筛选档(R2):全部 / 已连接 / 未启用。 */
export type PlatformFilter = "all" | "connected" | "disabled";

/** 一张平台卡的视图模型(网格渲染与筛选的最小完整单元)。 */
export interface PlatformCard {
  id: string;
  name: string;
  wave: "W1" | "W2" | "W3";
  status: PlatformCardStatus;
  /** 目录条数(仅已实装平台有;灰卡恒 0)。 */
  directoryCount: number;
  /** 钥匙链探测命中的凭据名(展示「已录几项」;空 = 无钥匙链证据)。 */
  matchedSecretNames: string[];
  guide: PlatformGuide | null;
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
// 展示常量(徽标文案/变体直白中文;变体复用现有 Badge 语义色)
// ---------------------------------------------------------------------------

const STATUS_LABEL: Record<PlatformCardStatus, string> = {
  connected: "已连接",
  needs_setup: "需要设置",
  coming_soon: "即将支持",
};

const STATUS_VARIANT: Record<PlatformCardStatus, "ok" | "warning" | "secondary"> = {
  connected: "ok",
  needs_setup: "warning",
  coming_soon: "secondary",
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
  /** channels.list 的 platforms 视图(平台 → 目录桶)。 */
  directory: Record<string, ChannelEntry[]>;
  /** secret.list 名单(凭据探测;钥匙链不可用时为空 = 降级无证据)。 */
  secretNames: string[];
}

/** 平台总览区:筛选 tabs + 平台卡片网格 + 可展开的出站凭据指南。 */
export function PlatformOverview({ status, directory, secretNames }: PlatformOverviewProps) {
  const [filter, setFilter] = useState<PlatformFilter>("all");
  /** 当前展开凭据指南的平台 id(一次一张;null = 全收起)。 */
  const [guideOpen, setGuideOpen] = useState<string | null>(null);

  const cards = useMemo(() => buildPlatformCards(directory, secretNames), [directory, secretNames]);
  const counts = useMemo(() => {
    const connected = cards.filter((card) => card.status === "connected").length;
    return { all: cards.length, connected, disabled: cards.length - connected };
  }, [cards]);
  const visible = useMemo(() => cards.filter((card) => matchesFilter(card, filter)), [cards, filter]);

  if (status === "error") return null;

  return (
    <div className="px-6" data-testid="platform-overview">
      <Card>
        <CardContent className="flex flex-col gap-3 p-4">
          <p className="flex items-center gap-1.5 text-sm font-medium text-foreground">
            <LayoutGrid className="size-4 text-muted-foreground" />
            平台总览
            <span className="text-xs font-normal text-muted-foreground">
              (已实装平台带真实状态与目录条数;灰卡平台按波次排期,尚未实装)
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
                    variant={filter === id ? "default" : "outline"}
                    aria-pressed={filter === id}
                    data-testid={`platform-filter-${id}`}
                    onClick={() => setFilter(id)}
                  >
                    {FILTER_LABEL[id]}({counts[id]})
                  </Button>
                ))}
              </div>

              <div className="grid grid-cols-[repeat(auto-fill,minmax(230px,1fr))] gap-2">
                {visible.map((card) => (
                  <PlatformCardView
                    key={card.id}
                    card={card}
                    guideOpen={guideOpen === card.id}
                    onToggleGuide={() =>
                      setGuideOpen((current) => (current === card.id ? null : card.id))
                    }
                  />
                ))}
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/** 单张平台卡:已实装 = 可展开凭据指南的交互卡;未实装 = 灰卡零交互。 */
function PlatformCardView({
  card,
  guideOpen,
  onToggleGuide,
}: {
  card: PlatformCard;
  guideOpen: boolean;
  onToggleGuide: () => void;
}) {
  const meta =
    card.status === "coming_soon" ? (
      <p className="text-[11px] text-muted-foreground">{card.wave} 波次排期中,尚未实装</p>
    ) : (
      <p className="text-[11px] text-muted-foreground">
        {card.directoryCount > 0
          ? `目录 ${card.directoryCount} 个会话`
          : "目录为空(先配凭据再刷新/等会话进入)"}
        {card.matchedSecretNames.length > 0
          ? `;钥匙链已录 ${card.matchedSecretNames.length} 项`
          : ""}
      </p>
    );

  if (card.status === "coming_soon" || !card.guide) {
    return (
      <div
        data-testid={`platform-card-${card.id}`}
        className="flex flex-col gap-1 rounded-md border border-border/60 bg-muted/30 p-3 opacity-70"
        title="尚未实装的平台(按 W2/W3 波次排期)"
      >
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium text-foreground">{card.name}</span>
          <Badge variant={STATUS_VARIANT[card.status]}>{STATUS_LABEL[card.status]}</Badge>
        </div>
        <p className="truncate font-mono text-[11px] text-muted-foreground">{card.id}</p>
        {meta}
      </div>
    );
  }

  return (
    <div
      data-testid={`platform-card-${card.id}`}
      className="flex flex-col gap-1 rounded-md border border-border/60 p-3"
    >
      <button
        type="button"
        className="flex flex-col items-start gap-1 text-left"
        aria-expanded={guideOpen}
        aria-label={`${card.name} 凭据指南`}
        onClick={onToggleGuide}
      >
        <div className="flex w-full items-center justify-between gap-2">
          <span className="truncate text-sm font-medium text-foreground">{card.name}</span>
          <Badge variant={STATUS_VARIANT[card.status]}>{STATUS_LABEL[card.status]}</Badge>
        </div>
        <p className="truncate font-mono text-[11px] text-muted-foreground">{card.id}</p>
        {meta}
        <p className="text-[11px] text-primary">{guideOpen ? "收起凭据指南" : "出站凭据指南"}</p>
      </button>
      {guideOpen ? <PlatformGuideView platformId={card.id} guide={card.guide} /> : null}
    </div>
  );
}

/** 出站凭据指南面板(R4):凭据 key 用途 + 获取步骤(中文直白)。 */
function PlatformGuideView({ platformId, guide }: { platformId: string; guide: PlatformGuide }) {
  return (
    <div
      className="flex flex-col gap-2 rounded-md bg-muted/30 p-2.5"
      data-testid={`platform-guide-${platformId}`}
    >
      <p className="text-xs font-medium text-foreground">出站凭据指南(定向推送只需要这些)</p>
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
