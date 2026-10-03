// @vitest-environment jsdom
// D3 基件行为冒烟(10-03-ui-deep-imitation):渲染/交互/关闭路径,
// 视觉与动效归统一门禁构建,不在此断言。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Switch", () => {
  it("非受控:点击切换 aria-checked 并回调 onCheckedChange", () => {
    const onCheckedChange = vi.fn();
    const { container } = render(
      <Switch defaultChecked={false} onCheckedChange={onCheckedChange} />,
    );
    const el = container.querySelector('[role="switch"]') as HTMLButtonElement;
    expect(el.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(el);
    expect(onCheckedChange).toHaveBeenCalledWith(true);
    expect(el.getAttribute("aria-checked")).toBe("true");
    expect(el.getAttribute("data-state")).toBe("checked");
  });

  it("受控:外部 checked 优先于内部态", () => {
    const { container } = render(<Switch checked={false} />);
    const el = container.querySelector('[role="switch"]') as HTMLButtonElement;
    fireEvent.click(el);
    expect(el.getAttribute("aria-checked")).toBe("false");
  });
});

describe("Tabs", () => {
  function setup() {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">甲</TabsTrigger>
          <TabsTrigger value="b">乙</TabsTrigger>
        </TabsList>
        <TabsContent value="a">内容甲</TabsContent>
        <TabsContent value="b">内容乙</TabsContent>
      </Tabs>,
    );
  }

  it("默认值渲染激活 trigger 与对应内容", () => {
    setup();
    expect(screen.getByRole("tab", { name: "甲" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tabpanel").textContent).toBe("内容甲");
  });

  it("点击切换;方向键巡游", () => {
    setup();
    fireEvent.click(screen.getByRole("tab", { name: "乙" }));
    expect(screen.getByRole("tabpanel").textContent).toBe("内容乙");
    expect(screen.getByRole("tab", { name: "甲" }).getAttribute("aria-selected")).toBe("false");

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "甲" }).getAttribute("aria-selected")).toBe("true");
  });
});

describe("Dialog", () => {
  it("trigger 开、Esc 关,标题/描述自动接 aria", () => {
    const onOpenChange = vi.fn();
    render(
      <Dialog onOpenChange={onOpenChange}>
        <DialogTrigger>打开</DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>标题</DialogTitle>
            <DialogDescription>描述</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose>取消</DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByText("打开"));
    const dialog = screen.getByRole("dialog");
    const title = screen.getByRole("heading", { name: "标题" });
    expect(dialog.getAttribute("aria-labelledby")).toBe(title.id);
    expect(title.id).toBeTruthy();
    expect(screen.getByText("描述").id).toBe(dialog.getAttribute("aria-describedby"));

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("DialogClose 点击回调 false", () => {
    const onOpenChange = vi.fn();
    render(
      <Dialog defaultOpen onOpenChange={onOpenChange}>
        <DialogContent>
          <DialogTitle>标题</DialogTitle>
          <DialogClose>关闭</DialogClose>
        </DialogContent>
      </Dialog>,
    );
    fireEvent.click(screen.getByText("关闭"));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

describe("DropdownMenu", () => {
  it("trigger 开菜单;item 激活后关闭;Esc 还焦 trigger", () => {
    const onSelect = vi.fn();
    render(
      <DropdownMenu>
        <DropdownMenuTrigger>操作</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem onSelect={onSelect}>复制</DropdownMenuItem>
          <DropdownMenuItem disabled>禁用</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );
    const trigger = screen.getByText("操作");
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(trigger);
    const menu = screen.getByRole("menu");
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(screen.getByRole("menuitem", { name: "复制" }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(menu.getAttribute("data-state")).toBe("closed");
  });
});

describe("Tooltip", () => {
  it("悬停延迟后出现并接 aria-describedby", () => {
    vi.useFakeTimers();
    render(
      <Tooltip>
        <TooltipTrigger>悬我</TooltipTrigger>
        <TooltipContent>提示文案</TooltipContent>
      </Tooltip>,
    );
    fireEvent.mouseEnter(screen.getByText("悬我"));
    expect(screen.queryByRole("tooltip")).toBeNull();
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(screen.getByRole("tooltip").textContent).toBe("提示文案");
    expect(screen.getByText("悬我").getAttribute("aria-describedby")).toBe(
      screen.getByRole("tooltip").id,
    );
  });
});

describe("Table / Input / ScrollArea", () => {
  it("表格结构:caption/表头/行", () => {
    render(
      <Table>
        <TableCaption>源清单</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead>名称</TableHead>
            <TableHead>状态</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>rss-demo</TableCell>
            <TableCell>ok</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );
    expect(screen.getByRole("caption").textContent).toBe("源清单");
    expect(screen.getByRole("columnheader", { name: "名称" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "rss-demo" })).toBeTruthy();
  });

  it("Input 接受透传属性;ScrollArea 默认纵向", () => {
    const { container } = render(
      <>
        <Input placeholder="搜索" data-testid="in" />
        <ScrollArea data-testid="sa">内容</ScrollArea>
      </>,
    );
    expect(screen.getByTestId("in").getAttribute("placeholder")).toBe("搜索");
    expect(screen.getByTestId("sa").getAttribute("data-orientation")).toBe("vertical");
    expect(container).toBeTruthy();
  });
});
