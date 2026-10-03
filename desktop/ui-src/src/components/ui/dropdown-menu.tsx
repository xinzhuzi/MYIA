import { Slot } from "@radix-ui/react-slot";
import * as React from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/utils";

/**
 * shadcn/ui dropdown-menu API,无 @radix-ui/react-dropdown-menu 依赖
 * (任务红线不装包;asChild 复用已装的 @radix-ui/react-slot)。自研点:
 * - 定位:fixed 锚定 trigger rect(side/align/sideOffset),滚动/缩放时重算,
 *   视口内夹紧;无碰撞翻转(桌面端基件够用,逐屏需要再升);
 * - 键盘:↑/↓/Home/End 巡游,Esc 关闭并还焦 trigger,Tab 关闭;
 * - 离场:播 pop-out 120ms(--duration-fast)后卸载。
 */

/** 与 --duration-fast(120ms)对应的离场卸载延迟,改 token 时同步改这里 */
const EXIT_UNMOUNT_MS = 120;

const ITEM_SELECTOR = '[role="menuitem"]:not([disabled])';

type DropdownContextValue = {
  open: boolean;
  setOpen: (open: boolean) => void;
  triggerRef: React.RefObject<HTMLButtonElement | null>;
};

const DropdownContext = React.createContext<DropdownContextValue | null>(null);

function useDropdownContext(component: string) {
  const ctx = React.useContext(DropdownContext);
  if (!ctx) throw new Error(`<${component}> 必须在 <DropdownMenu> 内使用`);
  return ctx;
}

function DropdownMenu({
  open: openProp,
  defaultOpen = false,
  onOpenChange,
  children,
  ...props
}: React.ComponentProps<"div"> & {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [internal, setInternal] = React.useState(defaultOpen);
  const isControlled = openProp !== undefined;
  const open = isControlled ? openProp : internal;
  const triggerRef = React.useRef<HTMLButtonElement>(null);

  const setOpen = React.useCallback(
    (next: boolean) => {
      if (!isControlled) setInternal(next);
      onOpenChange?.(next);
    },
    [isControlled, onOpenChange],
  );

  const ctx = React.useMemo(
    () => ({ open, setOpen, triggerRef }),
    [open, setOpen],
  );

  return (
    <DropdownContext.Provider value={ctx}>
      <div data-slot="dropdown-menu" className="relative inline-flex" {...props}>
        {children}
      </div>
    </DropdownContext.Provider>
  );
}

function DropdownMenuTrigger({
  asChild = false,
  onClick,
  onKeyDown,
  ...props
}: React.ComponentProps<"button"> & { asChild?: boolean }) {
  const { open, setOpen, triggerRef } = useDropdownContext("DropdownMenuTrigger");
  const Comp = asChild ? Slot : "button";

  function handleKeyDown(event: React.KeyboardEvent<HTMLButtonElement>) {
    onKeyDown?.(event);
    if (event.defaultPrevented) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
    }
  }

  return (
    <Comp
      ref={triggerRef}
      type="button"
      aria-haspopup="menu"
      aria-expanded={open}
      data-slot="dropdown-menu-trigger"
      data-state={open ? "open" : "closed"}
      {...props}
      onClick={(event: React.MouseEvent<HTMLButtonElement>) => {
        onClick?.(event);
        if (!event.defaultPrevented) setOpen(!open);
      }}
      onKeyDown={handleKeyDown}
    />
  );
}

function DropdownMenuContent({
  className,
  children,
  side = "bottom",
  align = "start",
  sideOffset = 6,
  onKeyDown,
  ...props
}: React.ComponentProps<"div"> & {
  side?: "top" | "bottom";
  align?: "start" | "center" | "end";
  sideOffset?: number;
}) {
  const { open, setOpen, triggerRef } = useDropdownContext("DropdownMenuContent");
  const contentRef = React.useRef<HTMLDivElement>(null);
  const [mounted, setMounted] = React.useState(open);
  const [state, setState] = React.useState<"open" | "closed">(open ? "open" : "closed");

  React.useEffect(() => {
    if (open) {
      setMounted(true);
      setState("open");
    } else {
      setState((prev) => (prev === "open" ? "closed" : prev));
    }
  }, [open]);

  React.useEffect(() => {
    if (!open && mounted) {
      const timer = window.setTimeout(() => setMounted(false), EXIT_UNMOUNT_MS);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [open, mounted]);

  // 锚定定位 + 外点关闭 + 开启聚焦
  React.useEffect(() => {
    if (!mounted) return undefined;
    const content = contentRef.current;

    function position() {
      const trigger = triggerRef.current;
      const node = contentRef.current;
      if (!trigger || !node) return;
      const rect = trigger.getBoundingClientRect();
      const { offsetWidth: w, offsetHeight: h } = node;
      let top = side === "bottom" ? rect.bottom + sideOffset : rect.top - sideOffset - h;
      let left =
        align === "end"
          ? rect.right - w
          : align === "center"
            ? rect.left + rect.width / 2 - w / 2
            : rect.left;
      const margin = 8;
      left = Math.min(Math.max(left, margin), window.innerWidth - w - margin);
      top = Math.min(Math.max(top, margin), window.innerHeight - h - margin);
      node.style.top = `${Math.round(top)}px`;
      node.style.left = `${Math.round(left)}px`;
    }

    function handleOutside(event: Event) {
      const target = event.target as Node;
      if (
        contentRef.current?.contains(target) ||
        triggerRef.current?.contains(target) ||
        !contentRef.current ||
        !triggerRef.current
      ) {
        return;
      }
      setOpen(false);
    }

    const raf = window.requestAnimationFrame(() => {
      position();
      content?.focus();
    });
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    document.addEventListener("pointerdown", handleOutside, true);
    return () => {
      window.cancelAnimationFrame(raf);
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
      document.removeEventListener("pointerdown", handleOutside, true);
    };
  }, [mounted, side, align, sideOffset, setOpen, triggerRef]);

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    onKeyDown?.(event);
    if (event.defaultPrevented) return;
    const items = contentRef.current
      ? [...contentRef.current.querySelectorAll<HTMLElement>(ITEM_SELECTOR)]
      : [];
    if (event.key === "Escape" || event.key === "Tab") {
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
      return;
    }
    if (items.length === 0) return;
    const index = items.indexOf(document.activeElement as HTMLElement);
    let next: HTMLElement | undefined;
    if (event.key === "ArrowDown") next = items[(index + 1 + items.length) % items.length];
    else if (event.key === "ArrowUp") next = items[(index - 1 + items.length) % items.length];
    else if (event.key === "Home") next = items[0];
    else if (event.key === "End") next = items[items.length - 1];
    if (next) {
      event.preventDefault();
      next.focus();
    }
  }

  if (!mounted) return null;

  return createPortal(
    <div
      ref={contentRef}
      role="menu"
      tabIndex={-1}
      data-slot="dropdown-menu-content"
      data-state={state}
      data-side={side}
      data-align={align}
      className={cn(
        "fixed z-50 min-w-32 max-w-64 overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-popover",
        "focus:outline-none",
        "data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out",
        className,
      )}
      onKeyDown={handleKeyDown}
      {...props}
    >
      {children}
    </div>,
    document.body,
  );
}

function DropdownMenuItem({
  className,
  variant = "default",
  onSelect,
  onClick,
  disabled,
  ...props
}: React.ComponentProps<"button"> & {
  variant?: "default" | "destructive";
}) {
  const { setOpen } = useDropdownContext("DropdownMenuItem");

  return (
    <button
      type="button"
      role="menuitem"
      data-variant={variant}
      disabled={disabled}
      data-slot="dropdown-menu-item"
      className={cn(
        "flex w-full cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm whitespace-nowrap select-none",
        "transition-colors duration-(--duration-fast) ease-out-expo",
        "focus:bg-accent focus:text-accent-foreground focus:outline-none",
        "data-[variant=destructive]:text-destructive data-[variant=destructive]:focus:bg-destructive/10",
        "disabled:pointer-events-none disabled:opacity-50",
        "[&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 [&_svg]:shrink-0",
        "[&>kbd]:ml-auto [&>kbd]:text-2xs [&>kbd]:font-normal [&>kbd]:text-muted-foreground",
        className,
      )}
      {...props}
      onClick={(event) => {
        onClick?.(event);
        if (event.defaultPrevented) return;
        onSelect?.(event);
        if (event.defaultPrevented) return;
        setOpen(false);
      }}
    />
  );
}

function DropdownMenuLabel({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dropdown-menu-label"
      className={cn("px-2 py-1.5 text-2xs text-muted-foreground", className)}
      {...props}
    />
  );
}

function DropdownMenuSeparator({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      data-slot="dropdown-menu-separator"
      className={cn("-mx-1 my-1 h-px bg-border", className)}
      {...props}
    />
  );
}

function DropdownMenuGroup({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      role="group"
      data-slot="dropdown-menu-group"
      className={cn("grid gap-0.5", className)}
      {...props}
    />
  );
}

export {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
};
