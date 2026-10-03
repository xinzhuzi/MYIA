// @vitest-environment jsdom
//
// 平台头像芯片(task 10-03-messaging-hermes-look R1)组件测试:
// 28 平台规格表全覆盖(键集与波次表一一对应)、telegram 精确官方标(SVG
// path 数据直接采用)、feishu 官方字形 monogram(飞书官方标即「飞」)、
// 未实装平台通用标灰态随波次(W2 全灰 / W3 弱一档)、未登记平台兜底
// monogram。品牌色是数据非主题 token;状态三色不在本文件(在 StatePill)。
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  monogramFor,
  PLATFORM_ICON_SPECS,
  PlatformAvatar,
} from "./platform-icons";
import { IMPLEMENTED_PLATFORMS, UPCOMING_PLATFORMS } from "./platform-overview";

// vitest 非 globals 模式下 RTL 不自动清理,防 DOM 跨用例污染(与消息屏主测试同范式)
afterEach(() => {
  cleanup();
});

describe("平台头像:规格表全覆盖", () => {
  it("28 平台全部有规格;已实装 6 家 brand 精确标,22 家 generic 通用标随波次", () => {
    const expected = [
      ...IMPLEMENTED_PLATFORMS.map((p) => p.id),
      ...UPCOMING_PLATFORMS.map((p) => p.id),
    ];
    expect(Object.keys(PLATFORM_ICON_SPECS).sort()).toEqual([...expected].sort());
    expect(Object.keys(PLATFORM_ICON_SPECS)).toHaveLength(28);
    // W2 转实装的四家(ntfy/钉钉/企微/微信)已从 UPCOMING 移入 IMPLEMENTED
    expect(IMPLEMENTED_PLATFORMS.map((p) => p.id)).toContain("ntfy");
    expect(IMPLEMENTED_PLATFORMS.map((p) => p.id)).toContain("weixin");
    expect(UPCOMING_PLATFORMS.map((p) => p.id)).not.toContain("weixin");

    for (const id of IMPLEMENTED_PLATFORMS.map((p) => p.id)) {
      const spec = PLATFORM_ICON_SPECS[id];
      expect(spec.kind).toBe("brand");
      expect(spec.color).toMatch(/^#[0-9A-Fa-f]{6}$/); // 品牌色是数据(官方主色)
    }
    const waves = new Set<string>();
    for (const platform of UPCOMING_PLATFORMS) {
      const spec = PLATFORM_ICON_SPECS[platform.id];
      expect(spec.kind).toBe("generic");
      expect(spec.Icon).toBeTruthy(); // 通用标必填(lucide 也是 SVG 组件)
      expect(spec.wave).toBe(platform.wave);
      waves.add(spec.wave ?? "");
    }
    expect(waves).toEqual(new Set(["W3"])); // W2 未实装已清零(微信转实装)
  });
});

describe("平台头像:芯片画法", () => {
  it("telegram 精确官方标:svg path 数据直接采用,viewBox 24×24,fill=currentColor", () => {
    const { container } = render(
      <PlatformAvatar platformId="telegram" platformName="Telegram" />,
    );
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    expect(svg?.getAttribute("viewBox")).toBe("0 0 24 24");
    expect(svg?.getAttribute("fill")).toBe("currentColor");
    // Simple Icons「Telegram」官方字形的开头(路径数据直接采用;一字符未改)
    expect(svg?.querySelector("path")?.getAttribute("d")).toMatch(/^M11\.944 0A12 12 0 0 0 0 12/);
    expect(svg?.querySelector("title")).toBeNull(); // aria-hidden 装饰性用,无 title
  });

  it("feishu 官方字形 monogram:品牌色 tint 底 +「飞」字符(飞书官方标即「飞」)", () => {
    const { container, getByTestId } = render(
      <PlatformAvatar platformId="feishu" platformName="飞书" />,
    );
    const chip = getByTestId("platform-avatar-feishu");
    expect(chip.textContent).toBe("飞");
    expect(chip.getAttribute("aria-hidden")).toBe("true");
    expect(container.querySelector("svg")).toBeNull(); // monogram 路线不出 svg
    // jsdom 会把 #3370FF 归一化为 rgb 形式;断言口径 = 品牌色 16% tint 底
    expect(chip.style.backgroundColor).toContain("color-mix(in srgb, rgb(51, 112, 255) 16%, transparent)");
    expect(chip.style.color).toContain("rgb(51, 112, 255)");
  });

  it("未实装平台通用标灰态随波次:W3 底与字形各再弱一档(W2 未实装已清零)", () => {
    // 微信随 10-03-messaging-weixin-bridge 转实装后,UPCOMING 只余 W3 波次
    expect(UPCOMING_PLATFORMS.filter((p) => p.wave === "W2")).toEqual([]);
    const { getByTestId } = render(<PlatformAvatar platformId="slack" platformName="Slack" />);
    const w3 = getByTestId("platform-avatar-slack");
    expect(w3.querySelector("svg")).toBeTruthy(); // lucide 通用标也是 SVG
    expect(w3.className).toContain("bg-muted/50");
    expect(w3.className).toContain("text-muted-foreground/60");
    expect(w3.className.split(/\s+/)).not.toContain("bg-muted"); // 不用全灰档
  });

  it("W2 转实装三家:ntfy 官方字形品牌色;钉钉/企微官方主色 monogram(与飞书同范式)", () => {
    const { getByTestId, rerender } = render(<PlatformAvatar platformId="ntfy" platformName="ntfy" />);
    const ntfy = getByTestId("platform-avatar-ntfy");
    // Simple Icons「ntfy」官方字形(SVG path 直接采用),品牌色 #317F6F(simple-icons 数据)
    expect(ntfy.querySelector("svg")?.getAttribute("viewBox")).toBe("0 0 24 24");
    expect(ntfy.style.color).toContain("rgb(49, 127, 111)"); // #317F6F
    expect(ntfy.textContent!.trim()).toBe(""); // 字形路线不出 monogram

    rerender(<PlatformAvatar platformId="dingtalk" platformName="钉钉" />);
    const dingtalk = getByTestId("platform-avatar-dingtalk");
    expect(dingtalk.textContent).toBe("钉"); // 品牌方无可用简化标 → 官方主色 + 字符
    expect(dingtalk.style.color).toContain("0, 137, 255"); // #0089FF 钉钉蓝

    rerender(<PlatformAvatar platformId="wecom" platformName="企业微信" />);
    const wecom = getByTestId("platform-avatar-wecom");
    expect(wecom.textContent).toBe("企");
    expect(wecom.style.color).toContain("38, 126, 240"); // #267EF0 企微标准蓝(官方色值表 blue_btn)

    // 微信(10-03-messaging-weixin-bridge 转实装):官方绿 + 「微」monogram
    rerender(<PlatformAvatar platformId="weixin" platformName="微信" />);
    const weixin = getByTestId("platform-avatar-weixin");
    expect(weixin.textContent).toBe("微");
    expect(weixin.style.color).toContain("7, 193, 96"); // #07C160 微信官方绿
  });

  it("未登记平台兜底:中性底 + 名称首字 monogram(上游 monogramFor 同语义)", () => {
    const { getByTestId } = render(
      <PlatformAvatar platformId="not-yet-registered" platformName="某新平台" />,
    );
    const chip = getByTestId("platform-avatar-not-yet-registered");
    expect(chip.textContent).toBe("某");
    expect(chip.className).toContain("bg-muted");
  });

  it("className 透传:调用方可改芯片尺寸(详情头/平台行共用同一画法)", () => {
    const { getByTestId } = render(
      <PlatformAvatar className="size-7" platformId="telegram" platformName="Telegram" />,
    );
    expect(getByTestId("platform-avatar-telegram").className).toContain("size-7");
  });
});

describe("平台头像:monogramFor 纯函数", () => {
  it("名称首字;中文取首汉字,西文取大写首字母,首尾空白剔除", () => {
    expect(monogramFor("飞书")).toBe("飞");
    expect(monogramFor("telegram")).toBe("T");
    expect(monogramFor("  Slack ")).toBe("S");
    expect(monogramFor("ntfy")).toBe("N");
  });
});
