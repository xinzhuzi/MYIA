// @vitest-environment jsdom
//
// 软件更新卡片测试(v1.1.1 更新通道 UI 接线,design.md §3.3 三态):
// 无更新(回显当前版本)/ 有更新 → 下载安装 → relaunch / 检查失败 → ErrorBox。
// 附:安装失败 → ErrorBox 回退可重试。全量 mock 两个插件模块 + getVersion,
// 不触真实 Tauri IPC(Rust 侧注册与签名闭环见 desktop/UPDATER.md,不在此测)。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  check: vi.fn(),
  relaunch: vi.fn(),
  getVersion: vi.fn(),
  downloadAndInstall: vi.fn(),
}));
vi.mock("@tauri-apps/plugin-updater", () => ({ check: mocks.check }));
vi.mock("@tauri-apps/plugin-process", () => ({ relaunch: mocks.relaunch }));
vi.mock("@tauri-apps/api/app", () => ({ getVersion: mocks.getVersion }));

import type { Update } from "@tauri-apps/plugin-updater";

import { UpdaterCard } from "./updater-card";

/** 假 Update:只带卡片用到的字段(结构对照 plugin-updater dist-js 的 UpdateMetadata)。 */
function fakeUpdate(overrides?: Partial<{ version: string; currentVersion: string; body?: string }>) {
  return {
    version: "1.1.2",
    currentVersion: "1.1.1",
    body: "Fixed desktop data paths; bundled demo plugin.",
    downloadAndInstall: mocks.downloadAndInstall,
    close: vi.fn(),
    ...overrides,
  } as unknown as Update;
}

beforeEach(() => {
  mocks.check.mockReset();
  mocks.relaunch.mockReset();
  mocks.getVersion.mockReset();
  mocks.downloadAndInstall.mockReset();
  mocks.getVersion.mockResolvedValue("1.1.1");
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("设置 · 软件更新", () => {
  it("无更新:回显「已是最新(当前 v1.1.1)」,不出安装按钮", async () => {
    mocks.check.mockResolvedValue(null);
    render(<UpdaterCard />);

    fireEvent.click(screen.getByRole("button", { name: "检查更新" }));

    const status = await screen.findByTestId("updater-status");
    expect(status.textContent).toContain("已是最新");
    expect(status.textContent).toContain("v1.1.1");
    expect(screen.queryByRole("button", { name: "下载并安装" })).toBeNull();
    expect(mocks.relaunch).not.toHaveBeenCalled();
  });

  it("有更新:展示版本与 notes → 下载并安装 → relaunch", async () => {
    mocks.check.mockResolvedValue(fakeUpdate());
    render(<UpdaterCard />);

    fireEvent.click(screen.getByRole("button", { name: "检查更新" }));

    const status = await screen.findByTestId("updater-status");
    expect(status.textContent).toContain("发现新版本");
    expect(status.textContent).toContain("v1.1.2");
    expect(status.textContent).toContain("v1.1.1"); // 当前版本对照
    expect(status.textContent).toContain("Fixed desktop data paths"); // notes(body)如实展示

    fireEvent.click(screen.getByRole("button", { name: "下载并安装" }));
    await waitFor(() => {
      expect(mocks.downloadAndInstall).toHaveBeenCalledTimes(1);
      expect(mocks.relaunch).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByTestId("updater-status").textContent).toContain("正在重启");
  });

  it("检查失败 → ErrorBox 结构化回显(code=updater_check_failed),可重试", async () => {
    mocks.check
      .mockRejectedValueOnce(new Error("endpoint unreachable"))
      .mockResolvedValueOnce(null);
    render(<UpdaterCard />);

    fireEvent.click(screen.getByRole("button", { name: "检查更新" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("endpoint unreachable");
    expect(alert.textContent).toContain("code=updater_check_failed");
    expect(screen.queryByTestId("updater-status")?.textContent).not.toContain("已是最新");

    // 重试按钮走同一条检查路径:第二次成功 → 已是最新
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => {
      expect(mocks.check).toHaveBeenCalledTimes(2);
    });
    expect(await screen.findByText(/已是最新/)).toBeTruthy();
  });

  it("安装失败 → ErrorBox(code=updater_install_failed),回到可重装态", async () => {
    mocks.check.mockResolvedValue(fakeUpdate());
    mocks.downloadAndInstall.mockRejectedValue(new Error("signature verification failed"));
    render(<UpdaterCard />);

    fireEvent.click(screen.getByRole("button", { name: "检查更新" }));
    await screen.findByText(/发现新版本/);
    fireEvent.click(screen.getByRole("button", { name: "下载并安装" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("signature verification failed");
    expect(alert.textContent).toContain("code=updater_install_failed");
    expect(mocks.relaunch).not.toHaveBeenCalled();
    // 失败回退 available:可再次安装
    expect(screen.getByRole("button", { name: "下载并安装" })).toBeTruthy();
  });
});
