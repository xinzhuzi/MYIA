// @vitest-environment jsdom
//
// EditorPane 真实渲染集成(不 mock CodeMirror):jsdom 下 CodeMirror 6 能
// 完成挂载与文档渲染(measure 阶段的 getClientRects 告警属 jsdom 不完整
// Range API,不影响断言);屏层逻辑测试见 yaml-editor-screen.test.tsx
// (那边以受控 textarea 替身换稳定的打字交互)。
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EditorPane } from "./editor-pane";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("EditorPane(CodeMirror 封装)", () => {
  it("YAML 编辑器渲染:原文入文档 + 行号槽 + 受控值", () => {
    render(<EditorPane value={"id: demo\nname: 演示"} onChange={() => {}} />);

    const host = document.querySelector('[data-testid="yaml-editor"]');
    expect(host).toBeTruthy();
    expect(host?.querySelector(".cm-editor")).toBeTruthy(); // CodeMirror 真实挂载
    expect(host?.querySelector(".cm-content")?.textContent).toContain("id: demo");
    expect(host?.querySelector(".cm-content")?.textContent).toContain("name: 演示");
    expect(host?.querySelector(".cm-lineNumbers")).toBeTruthy(); // 行号(design §3)
  });

  it("受控契约:value 变更重渲染新文档;onChange 收到编辑事件", () => {
    const onChange = vi.fn();
    const { rerender } = render(<EditorPane value={"a: 1"} onChange={onChange} />);
    expect(document.querySelector(".cm-content")?.textContent).toContain("a: 1");

    rerender(<EditorPane value={"a: 2\nb: 3"} onChange={onChange} />);
    expect(document.querySelector(".cm-content")?.textContent).toContain("b: 3");

    // 编辑事件:CodeMirror 的 DOM 事件管线在 jsdom 下不可合成真实键盘输入,
    // onChange 路径由屏层测试的替身覆盖;此处锁定受控 value 单向即可
    expect(onChange).not.toHaveBeenCalled();
  });
});
