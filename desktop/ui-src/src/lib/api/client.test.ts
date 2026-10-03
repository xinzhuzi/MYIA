// @vitest-environment jsdom
//
// 共享 client 的 feed-ux 批新增封装测试(10-03-feed-ux 步骤 4):
// feedExport / schedulePreview / pushTest 三封装 —— 方法名与参数形状透传
// (契约对账 entry.py `_HANDLERS` 同名方法),错误经 SidecarRequestError 归一。
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ invoke: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn() }));

import { api, SidecarRequestError } from "@/lib/api";

beforeEach(() => {
  mocks.invoke.mockReset();
});

describe("api 门面方法集(对账 _HANDLERS 键)", () => {
  it("feed-ux 三方法在门面上(与 entry.py 注册同名)", () => {
    expect(typeof api.feedExport).toBe("function");
    expect(typeof api.schedulePreview).toBe("function");
    expect(typeof api.pushTest).toBe("function");
  });
});

describe("feedExport(G3)", () => {
  it("透传 format/path + 当前过滤视图(category × query)到 feed.export", async () => {
    mocks.invoke.mockResolvedValue({ path: "/tmp/f.jsonl", count: 3, bytes: 900 });
    const result = await api.feedExport({
      format: "jsonl",
      path: "/tmp/f.jsonl",
      category: "news",
      query: "rss",
    });
    expect(mocks.invoke).toHaveBeenCalledWith("sidecar_request", {
      method: "feed.export",
      params: { format: "jsonl", path: "/tmp/f.jsonl", category: "news", query: "rss" },
    });
    expect(result.count).toBe(3);
  });

  it("export_path_invalid 经 SidecarRequestError 抛出(code 原文)", async () => {
    mocks.invoke.mockRejectedValue(
      JSON.stringify({ code: "export_path_invalid", path: "params.path", message: "导出路径必须是绝对路径" }),
    );
    const error = await api.feedExport({ format: "csv", path: "relative.csv" }).catch((err) => err);
    expect(error).toBeInstanceOf(SidecarRequestError);
    expect(error.code).toBe("export_path_invalid");
  });
});

describe("schedulePreview(G4)", () => {
  it("透传 file/count 到 schedule.preview;result 形状透传", async () => {
    mocks.invoke.mockResolvedValue({
      file: "/p/demo.yaml",
      schedule: "0 9 * * *",
      timezone: null,
      runs: ["2026-10-04T09:00:00+08:00"],
    });
    const result = await api.schedulePreview({ file: "/p/demo.yaml", count: 5 });
    expect(mocks.invoke).toHaveBeenCalledWith("sidecar_request", {
      method: "schedule.preview",
      params: { file: "/p/demo.yaml", count: 5 },
    });
    expect(result.schedule).toBe("0 9 * * *");
    expect(result.runs).toHaveLength(1);
  });
});

describe("pushTest(G5 前半)", () => {
  it("透传 channel/target 到 push.test;stdout 通道 preview 回显", async () => {
    mocks.invoke.mockResolvedValue({ ok: true, channel: "stdout", preview: "MYIA 推送测试" });
    const result = await api.pushTest({ channel: "stdout" });
    expect(mocks.invoke).toHaveBeenCalledWith("sidecar_request", {
      method: "push.test",
      params: { channel: "stdout" },
    });
    expect(result.preview).toContain("MYIA 推送测试");
  });

  it("通道结构化错误(如 env_var_missing)code 原文透传", async () => {
    mocks.invoke.mockRejectedValue(
      JSON.stringify({ code: "env_var_missing", path: "params.channel", message: "FEISHU_BOT_TOKEN 未设置" }),
    );
    const error = await api.pushTest({ channel: "feishu_card" }).catch((err) => err);
    expect(error).toBeInstanceOf(SidecarRequestError);
    expect(error.code).toBe("env_var_missing");
  });
});
