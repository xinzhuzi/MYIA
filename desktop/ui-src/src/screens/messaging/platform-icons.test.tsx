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
  it("28 平台全部有规格;已实装 2 家 brand 精确标,26 家 generic 通用标随波次", () => {
    const expected = [
      ...IMPLEMENTED_PLATFORMS.map((p) => p.id),
      ...UPCOMING_PLATFORMS.map((p) => p.id),
    ];
    expect(Object.keys(PLATFORM_ICON_SPECS).sort()).toEqual([...expected].sort());
    expect(Object.keys(PLATFORM_ICON_SPECS)).toHaveLength(28);

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
      waves.add(platform.wave);
    }
    expect(waves).toEqual(new Set(["W2", "W3"]));
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

  it("未实装平台通用标灰态随波次:W2 全灰,W3 底与字形各再弱一档", () => {
    const { rerender, getByTestId } = render(
      <PlatformAvatar platformId="weixin" platformName="微信" />,
    );
    const w2 = getByTestId("platform-avatar-weixin");
    expect(w2.querySelector("svg")).toBeTruthy(); // lucide 通用标也是 SVG
    // 类名数组精确匹配(bg-muted/50 的子串会误判命中 bg-muted)
    expect(w2.className.split(/\s+/)).toContain("bg-muted");
    expect(w2.className.split(/\s+/)).not.toContain("bg-muted/50");

    rerender(<PlatformAvatar platformId="slack" platformName="Slack" />);
    const w3 = getByTestId("platform-avatar-slack");
    expect(w3.className).toContain("bg-muted/50");
    expect(w3.className).toContain("text-muted-foreground/60");
    expect(w3.className.split(/\s+/)).not.toContain("bg-muted"); // W3 不用全灰档
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
