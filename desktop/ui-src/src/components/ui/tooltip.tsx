import { Slot } from "@radix-ui/react-slot";
import * as React from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/utils";

/**
 * shadcn/ui tooltip API,无 @radix-ui/react-tooltip 依赖(任务红线不装包;
 * asChild 复用已装的 @radix-ui/react-slot)。自研点:
 * - 悬停 300ms 开 / 80ms 关;键盘焦点(:focus-visible)同样触发;
 * - 定位 fixed 锚定 trigger rect(side/align/sideOffset),滚动/缩放重算;
 * - 开场 fade-in 120ms(--duration-fast),离场播完再卸载。
 */

/** 与 --duration-fast(120ms)对应的离场卸载延迟,改 token 时同步改这里 */
const EXIT_UNMOUNT_MS = 120;

type TooltipContextValue = {
  open: boolean;
  setOpen: (open: boolean) => void;
  triggerRef: React.RefObject<HTMLElement | null>;
  contentId: string;
};

const TooltipContext = React.createContext<TooltipContextValue | null>(null);

function useTooltipContext(component: string) {
  const ctx = React.useContext(TooltipContext);
  if (!ctx) throw new Error(`<${component}> 必须在 <Tooltip> 内使用`);
  return ctx;
}

function Tooltip({
  open: openProp,
  defaultOpen = false,
  onOpenChange,
  openDelay = 300,
  closeDelay = 80,
  children,
  ...props
}: React.ComponentProps<"span"> & {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  openDelay?: number;
  closeDelay?: number;
}) {
  const [internal, setInternal] = React.useState(defaultOpen);
  const isControlled = openProp !== undefined;
  const open = isControlled ? openProp : internal;
  const triggerRef = React.useRef<HTMLElement>(null);
  const contentId = React.useId();
  const timersRef = React.useRef<{ open?: number; close?: number }>({});

  const setOpenRaw = React.useCallback(
    (next: boolean) => {
      if (!isControlled) setInternal(next);
      onOpenChange?.(next);
    },
    [isControlled, onOpenChange],
  );

  const setOpen = React.useCallback(
    (next: boolean) => {
      const timers = timersRef.current;
      if (timers.open) window.clearTimeout(timers.open);
      if (timers.close) window.clearTimeout(timers.close);
      if (next) {
        timers.open = window.setTimeout(() => setOpenRaw(true), openDelay);
      } else {
        timers.close = window.setTimeout(() => setOpenRaw(false), closeDelay);
      }
    },
    [openDelay, closeDelay, setOpenRaw],
  );

  React.useEffect(() => {
    const timers = timersRef.current;
    return () => {
      if (timers.open) window.clearTimeout(timers.open);
      if (timers.close) window.clearTimeout(timers.close);
    };
  }, []);

  const ctx = React.useMemo(
    () => ({ open, setOpen, triggerRef, contentId }),
    [open, setOpen, contentId],
  );

  return (
    <TooltipContext.Provider value={ctx}>
      <span data-slot="tooltip" className="inline-flex" {...props}>
        {children}
      </span>
    </TooltipContext.Provider>
  );
}

function TooltipTrigger({
  asChild = false,
  onMouseEnter,
  onMouseLeave,
  onFocus,
  onBlur,
  onKeyDown,
  onPointerDown,
  ...props
}: React.ComponentProps<"button"> & { asChild?: boolean }) {
  const { open, setOpen, triggerRef, contentId } = useTooltipContext("TooltipTrigger");
  const Comp = asChild ? Slot : "button";

  return (
    <Comp
      ref={triggerRef as React.Ref<HTMLButtonElement>}
      type="button"
      aria-describedby={open ? contentId : undefined}
      data-slot="tooltip-trigger"
      data-state={open ? "delayed-open" : "closed"}
      {...props}
      onMouseEnter={(event: React.MouseEvent<HTMLButtonElement>) => {
        onMouseEnter?.(event);
        setOpen(true);
      }}
      onMouseLeave={(event: React.MouseEvent<HTMLButtonElement>) => {
        onMouseLeave?.(event);
        setOpen(false);
      }}
      onFocus={(event: React.FocusEvent<HTMLButtonElement>) => {
        onFocus?.(event);
        if (event.target.matches(":focus-visible")) setOpen(true);
      }}
      onBlur={(event: React.FocusEvent<HTMLButtonElement>) => {
        onBlur?.(event);
        setOpen(false);
      }}
      onKeyDown={(event: React.KeyboardEvent<HTMLButtonElement>) => {
        onKeyDown?.(event);
        if (!event.defaultPrevented && event.key === "Escape") setOpen(false);
      }}
      onPointerDown={(event: React.PointerEvent<HTMLButtonElement>) => {
        onPointerDown?.(event);
        setOpen(false);
      }}
    />
  );
}

function TooltipContent({
  className,
  children,
  side = "top",
  align = "center",
  sideOffset = 6,
  ...props
}: React.ComponentProps<"div"> & {
  side?: "top" | "bottom" | "left" | "right";
  align?: "start" | "center" | "end";
  sideOffset?: number;
}) {
  const { open, triggerRef, contentId } = useTooltipContext("TooltipContent");
  const contentRef = React.useRef<HTMLDivElement>(null);
  const [mounted, setMounted] = React.useState(open);
  const [state, setState] = React.useState<"open" | "closed">(open ? "open" : "closed");
  const [positioned, setPositioned] = React.useState(false);

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

  React.useEffect(() => {
    if (!mounted) {
      setPositioned(false);
      return undefined;
    }

    function position() {
      const trigger = triggerRef.current;
      const node = contentRef.current;
      if (!trigger || !node) return;
      const rect = trigger.getBoundingClientRect();
      const { offsetWidth: w, offsetHeight: h } = node;
      let top: number;
      let left: number;
      if (side === "top") {
        top = rect.top - sideOffset - h;
        left = align === "start" ? rect.left : align === "end" ? rect.right - w : rect.left + rect.width / 2 - w / 2;
      } else if (side === "bottom") {
        top = rect.bottom + sideOffset;
        left = align === "start" ? rect.left : align === "end" ? rect.right - w : rect.left + rect.width / 2 - w / 2;
      } else if (side === "left") {
        top = align === "start" ? rect.top : align === "end" ? rect.bottom - h : rect.top + rect.height / 2 - h / 2;
        left = rect.left - sideOffset - w;
      } else {
        top = align === "start" ? rect.top : align === "end" ? rect.bottom - h : rect.top + rect.height / 2 - h / 2;
        left = rect.right + sideOffset;
      }
      const margin = 8;
      node.style.top = `${Math.round(Math.min(Math.max(top, margin), Math.max(margin, window.innerHeight - h - margin)))}px`;
      node.style.left = `${Math.round(Math.min(Math.max(left, margin), Math.max(margin, window.innerWidth - w - margin)))}px`;
      setPositioned(true);
    }

    const raf = window.requestAnimationFrame(position);
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    return () => {
      window.cancelAnimationFrame(raf);
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
    };
  }, [mounted, side, align, sideOffset, triggerRef]);

  if (!mounted) return null;

  return createPortal(
    <div
      ref={contentRef}
      id={contentId}
      role="tooltip"
      data-slot="tooltip-content"
      data-state={state}
      data-side={side}
      className={cn(
        "fixed z-50 w-fit max-w-64 rounded-md border border-border bg-popover px-2.5 py-1 text-xs text-popover-foreground shadow-popover",
        "data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out",
        positioned ? "opacity-100" : "opacity-0",
        className,
      )}
      {...props}
    >
      {children}
    </div>,
    document.body,
  );
}

export { Tooltip, TooltipContent, TooltipTrigger };
