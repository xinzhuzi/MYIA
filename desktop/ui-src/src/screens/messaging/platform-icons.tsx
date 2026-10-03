/**
 * 平台头像(task 10-03-messaging-hermes-look R1)—— 平台身份芯片,画法照上游
 * Hermes 蓝本(~/.hermes/hermes-agent/apps/desktop/src/app/messaging/platform-icon.tsx
 * + components/ui/avatar-chip.tsx)在 MYIA 栈内重排:
 *
 * PlatformAvatar = 24px 方形芯片(size-6 + rounded-md + place-items-center):
 *  - kind="brand" 精确标:品牌色 16% tint 底(color-mix)+ 原生品牌色画字形,
 *    字形约 14px(size-[58%],24px 芯片内——上游 GLYPH_CLASS 同比例);
 *  - kind="brand" + monogram:品牌方未发布可用简化标(Simple Icons 已按品牌方
 *    要求移除飞书)→ 品牌色底上画官方字形——飞书官方标本身就是「飞」字符,
 *    与上游对 Slack 的 monogram 处理同一范式;
 *  - kind="generic" 通用标:未实装平台(W2/W3)用 lucide 通用图标,灰态随
 *    波次(W2 全灰 bg-muted / W3 再弱一档 bg-muted/50)。
 *
 * 图标即数据(定调:SVG 路径数据直接采用):Telegram 字形逐字符采用
 * Simple Icons 官方字形(采集自 @icons-pack/react-simple-icons 13.11.1 的
 * SiTelegram.mjs,viewBox 0 0 24 24 / fill currentColor / 品牌色 #26A5E4),
 * 不引第三方图标包——依赖红线;飞书品牌蓝 #3370FF 为开放平台官方主色。
 * 状态三色(绿/黄/灰)不在此文件——那走 MYIA 语义 tokens(platform-overview
 * 的 StatePill),品牌色与状态色两套语言互不混用(与上游一致)。
 */
import { Bot, Hash, Home, Mail, MessageSquareText, Webhook } from "lucide-react";
import type { ComponentType, SVGProps } from "react";

import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// 精确官方标(SVG 路径数据直接采用;图标是数据,一字符未改)
// ---------------------------------------------------------------------------

/** Simple Icons「Telegram」官方字形(fill=currentColor,24×24;路径数据直接采用)。 */
function TelegramGlyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg fill="currentColor" viewBox="0 0 24 24" {...props}>
      <path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z" />
    </svg>
  );
}

/** Simple Icons「ntfy」官方字形(fill=currentColor,24×24;路径数据直接采用,
 * 采集自 simple-icons develop 分支 icons/ntfy.svg——依赖红线,不引图标包)。 */
function NtfyGlyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg fill="currentColor" viewBox="0 0 24 24" {...props}>
      <path d="M12.597 13.693v2.156h6.205v-2.156ZM5.183 6.549v2.363l3.591 1.901.023.01-.023.009-3.591 1.901v2.35l.386-.211 5.456-2.969V9.729ZM3.659 2.037C1.915 2.037.42 3.41.42 5.154v.002L.438 18.73 0 21.963l5.956-1.583h14.806c1.744 0 3.238-1.374 3.238-3.118V5.154c0-1.744-1.493-3.116-3.237-3.117h-.001zm0 2.2h17.104c.613.001 1.037.447 1.037.917v12.108c0 .47-.424.916-1.038.916H5.633l-3.026.915.031-.179-.017-13.76c0-.47.424-.917 1.038-.917z" />
    </svg>
  );
}

/** 未登记平台兜底:名称首字 monogram(上游 monogramFor 同语义;中文取首汉字)。 */
export const monogramFor = (name: string): string => name.trim().charAt(0).toUpperCase();

// ---------------------------------------------------------------------------
// 平台图标规格表(28 平台全覆盖;清单与 platform-overview 波次表同源)
// ---------------------------------------------------------------------------

/** 头像芯片规格:brand = 品牌色底画精确标/monogram;generic = 灰通用标随波次。 */
export interface PlatformIconSpec {
  /** 品牌字形(精确官方标);generic 条目必填,lucide 通用图标也是 SVG 组件。 */
  Icon?: ComponentType<SVGProps<SVGSVGElement>>;
  /** 品牌原生色(品牌色是数据,非主题 token;仅 brand 用)。 */
  color?: string;
  kind: "brand" | "generic";
  /** 品牌方未发布可用字形时的官方字形 monogram(如飞书官方标即「飞」)。 */
  monogram?: string;
  /** generic 专属:灰态随波次(W2 全灰 / W3 弱一档)。 */
  wave?: "W2" | "W3";
}

/** 28 平台(5 已实装精确标 + 23 未实装通用标);键与 IMPLEMENTED/UPCOMING 平台 id 一一对应。
 * W2 转实装(task 10-03-messaging-w2-platforms):ntfy = Simple Icons 官方字形
 * (品牌色 #317F6F,simple-icons 数据);钉钉/企微品牌方无可用简化标(与飞书
 * 同款 monogram 处理)——钉钉蓝 #0089FF(开放平台主站主色)、企微标准蓝
 * #267EF0(官方「应用色值表」blue_btn,developer.work.weixin.qq.com 94594)。 */
export const PLATFORM_ICON_SPECS: Record<string, PlatformIconSpec> = {
  // —— 已实装精确标 ——
  feishu: { color: "#3370FF", kind: "brand", monogram: "飞" },
  telegram: { Icon: TelegramGlyph, color: "#26A5E4", kind: "brand" },
  ntfy: { Icon: NtfyGlyph, color: "#317F6F", kind: "brand" },
  dingtalk: { color: "#0089FF", kind: "brand", monogram: "钉" },
  wecom: { color: "#267EF0", kind: "brand", monogram: "企" },
  // 微信随 10-03-messaging-weixin-bridge 转实装:generic → brand 精确标
  // (官方绿 #07C160 + 「微」monogram,钉钉/企微同范式)
  weixin: { color: "#07C160", kind: "brand", monogram: "微" },
  // —— 未实装 W3(远期)通用标(按形态分组;未知形态用默认消息标) ——
  slack: { Icon: Hash, kind: "generic", wave: "W3" },
  discord: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  whatsapp_cloud: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  signal: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  line: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  matrix: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  mattermost: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  google_chat: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  teams: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  email: { Icon: Mail, kind: "generic", wave: "W3" },
  sms: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  irc: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  simplex: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  bluebubbles: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  qqbot: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  yuanbao: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  a2a: { Icon: Bot, kind: "generic", wave: "W3" },
  photon: { Icon: Bot, kind: "generic", wave: "W3" },
  homeassistant: { Icon: Home, kind: "generic", wave: "W3" },
  msgraph_webhook: { Icon: Webhook, kind: "generic", wave: "W3" },
  buzz: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
  raft: { Icon: MessageSquareText, kind: "generic", wave: "W3" },
};

// ---------------------------------------------------------------------------
// 芯片组件(上游 AvatarChip 画法:品牌色 16% tint + currentColor 字形)
// ---------------------------------------------------------------------------

interface PlatformAvatarProps {
  platformId: string;
  platformName: string;
  className?: string;
}

/**
 * 平台身份芯片:平台行/详情头/目录速览共用同一画法(上游「一种画法,处处
 * 同款」的纪律)。24px 方形;brand 用品牌色 tint 底,generic 用灰底随波次,
 * 未登记平台用中性底 + 名称首字 monogram 兜底。
 */
export function PlatformAvatar({ className, platformId, platformName }: PlatformAvatarProps) {
  const spec = PLATFORM_ICON_SPECS[platformId];
  const chipClass = cn(
    "relative inline-grid size-6 shrink-0 place-items-center rounded-md font-medium",
    className,
  );

  if (!spec) {
    return (
      <span
        aria-hidden="true"
        className={cn(chipClass, "bg-muted text-xs text-muted-foreground")}
        data-testid={`platform-avatar-${platformId}`}
      >
        {monogramFor(platformName)}
      </span>
    );
  }

  if (spec.kind === "generic") {
    const Icon = spec.Icon!;
    return (
      <span
        aria-hidden="true"
        className={cn(
          chipClass,
          // 灰态随波次:W2 全灰;W3 底与字形各再弱一档
          spec.wave === "W2" ? "bg-muted text-muted-foreground" : "bg-muted/50 text-muted-foreground/60",
        )}
        data-testid={`platform-avatar-${platformId}`}
      >
        <Icon className="size-3.5" />
      </span>
    );
  }

  const Icon = spec.Icon;
  return (
    <span
      aria-hidden="true"
      className={chipClass}
      data-testid={`platform-avatar-${platformId}`}
      style={{
        // 品牌色 16% tint 底(上游 AvatarChip 同比例);品牌色是数据非主题 token
        backgroundColor: `color-mix(in srgb, ${spec.color} 16%, transparent)`,
        color: spec.color,
      }}
    >
      {Icon ? <Icon className="size-[58%]" /> : (spec.monogram ?? monogramFor(platformName))}
    </span>
  );
}
