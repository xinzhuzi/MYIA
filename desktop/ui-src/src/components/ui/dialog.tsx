import { Slot } from "@radix-ui/react-slot";
import { XIcon } from "lucide-react";
import * as React from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/utils";

/**
 * shadcn/ui dialog API,无 @radix-ui/react-dialog 依赖(任务红线不装包;
 * asChild 复用已装的 @radix-ui/react-slot)。自研点:
 * - 遮罩即居中容器(flex),内容动画的 transform 不与定位 translate 打架;
 * - 焦点:开启聚焦内容、Tab 圈禁、Esc 关闭、关闭后焦点还给触发器;
 * - 离场动画:open=false 后保持挂载 120ms(--duration-fast)播 dialog-out 再卸载;
 * - 滚动锁定:挂载期间 body overflow hidden。
 * 源管理 YAML 编辑自研 dialog 归并到本件(design D3)。
 */

/** 与 --duration-fast(120ms)对应的离场卸载延迟,改 token 时同步改这里 */
const EXIT_UNMOUNT_MS = 120;

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

type DialogContextValue = {
  open: boolean;
  setOpen: (open: boolean) => void;
  baseId: string;
  titleId: string;
  descriptionId: string;
  hasTitle: boolean;
  hasDescription: boolean;
  notifyTitle: (present: boolean) => void;
  notifyDescription: (present: boolean) => void;
};

const DialogContext = React.createContext<DialogContextValue | null>(null);

function useDialogContext(component: string) {
  const ctx = React.useContext(DialogContext);
  if (!ctx) throw new Error(`<${component}> 必须在 <Dialog> 内使用`);
  return ctx;
}

function Dialog({
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
  const baseId = React.useId();
  const [hasTitle, setHasTitle] = React.useState(false);
  const [hasDescription, setHasDescription] = React.useState(false);

  const setOpen = React.useCallback(
    (next: boolean) => {
      if (!isControlled) setInternal(next);
      onOpenChange?.(next);
    },
    [isControlled, onOpenChange],
  );

  const ctx = React.useMemo<DialogContextValue>(
    () => ({
      open,
      setOpen,
      baseId,
      titleId: `${baseId}-title`,
      descriptionId: `${baseId}-description`,
      hasTitle,
      hasDescription,
      notifyTitle: setHasTitle,
      notifyDescription: setHasDescription,
    }),
    [open, setOpen, baseId, hasTitle, hasDescription],
  );

  return (
    <DialogContext.Provider value={ctx}>
      <div data-slot="dialog" {...props}>
        {children}
      </div>
    </DialogContext.Provider>
  );
}

function DialogTrigger({
  asChild = false,
  onClick,
  ...props
}: React.ComponentProps<"button"> & { asChild?: boolean }) {
  const { setOpen } = useDialogContext("DialogTrigger");
  const Comp = asChild ? Slot : "button";

  return (
    <Comp
      type="button"
      aria-haspopup="dialog"
      data-slot="dialog-trigger"
      {...props}
      onClick={(event: React.MouseEvent<HTMLButtonElement>) => {
        onClick?.(event);
        if (!event.defaultPrevented) setOpen(true);
      }}
    />
  );
}

function DialogClose({
  asChild = false,
  onClick,
  ...props
}: React.ComponentProps<"button"> & { asChild?: boolean }) {
  const { setOpen } = useDialogContext("DialogClose");
  const Comp = asChild ? Slot : "button";

  return (
    <Comp
      type="button"
      data-slot="dialog-close"
      {...props}
      onClick={(event: React.MouseEvent<HTMLButtonElement>) => {
        onClick?.(event);
        if (!event.defaultPrevented) setOpen(false);
      }}
    />
  );
}

function DialogContent({
  className,
  children,
  showCloseButton = true,
  onKeyDown,
  ...props
}: React.ComponentProps<"div"> & { showCloseButton?: boolean }) {
  const { open, setOpen, titleId, descriptionId, hasTitle, hasDescription } =
    useDialogContext("DialogContent");
  const overlayRef = React.useRef<HTMLDivElement>(null);
  const contentRef = React.useRef<HTMLDivElement>(null);
  const restoreFocusRef = React.useRef<HTMLElement | null>(null);
  const [mounted, setMounted] = React.useState(open);
  const [state, setState] = React.useState<"open" | "closed">(open ? "open" : "closed");

  // 开→播 in;关→保持挂载播 out,120ms 后卸载(再开则撤销卸载)
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

  // 滚动锁定 + 初始聚焦 + 关闭后焦点还给开启前的元素
  React.useEffect(() => {
    if (!mounted) return undefined;
    restoreFocusRef.current = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const raf = window.requestAnimationFrame(() => contentRef.current?.focus());
    return () => {
      window.cancelAnimationFrame(raf);
      document.body.style.overflow = previousOverflow;
      const target = restoreFocusRef.current;
      if (target && document.contains(target)) target.focus?.();
    };
  }, [mounted]);

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    onKeyDown?.(event);
    if (event.defaultPrevented) return;
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      return;
    }
    if (event.key === "Tab" && overlayRef.current) {
      const focusables = [
        ...overlayRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      ].filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (focusables.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !overlayRef.current.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !overlayRef.current.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  if (!mounted) return null;

  return createPortal(
    <div
      ref={overlayRef}
      data-slot="dialog-overlay"
      data-state={state}
      className={cn(
        "fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4",
        "data-[state=open]:animate-overlay-in data-[state=closed]:animate-overlay-out",
      )}
      onKeyDown={handleKeyDown}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !event.defaultPrevented) setOpen(false);
      }}
    >
      <div
        ref={contentRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        data-slot="dialog-content"
        data-state={state}
        aria-labelledby={hasTitle ? titleId : undefined}
        aria-describedby={hasDescription ? descriptionId : undefined}
        className={cn(
          "relative grid w-full max-w-lg gap-4 rounded-lg border border-border bg-card p-6 text-card-foreground shadow-drawer",
          "data-[state=open]:animate-dialog-in data-[state=closed]:animate-dialog-out",
          "focus:outline-none",
          className,
        )}
        {...props}
      >
        {children}
        {showCloseButton && (
          <button
            type="button"
            aria-label="关闭"
            data-slot="dialog-close-button"
            className={cn(
              "absolute top-4 right-4 inline-flex size-6 items-center justify-center rounded-sm text-muted-foreground",
              "transition-colors duration-(--duration-fast) ease-out-expo hover:text-foreground",
            )}
            onClick={() => setOpen(false)}
          >
            <XIcon className="size-4" />
          </button>
        )}
      </div>
    </div>,
    document.body,
  );
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-header"
      className={cn("flex flex-col gap-1.5 pr-8 text-left", className)}
      {...props}
    />
  );
}

function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-footer"
      className={cn("flex flex-col-reverse gap-2 sm:flex-row sm:justify-end", className)}
      {...props}
    />
  );
}

function DialogTitle({ className, ...props }: React.ComponentProps<"h2">) {
  const { titleId, notifyTitle } = useDialogContext("DialogTitle");

  React.useEffect(() => {
    notifyTitle(true);
    return () => notifyTitle(false);
  }, [notifyTitle]);

  return (
    <h2
      id={titleId}
      data-slot="dialog-title"
      className={cn("text-lg font-semibold tracking-tight", className)}
      {...props}
    />
  );
}

function DialogDescription({ className, ...props }: React.ComponentProps<"p">) {
  const { descriptionId, notifyDescription } = useDialogContext("DialogDescription");

  React.useEffect(() => {
    notifyDescription(true);
    return () => notifyDescription(false);
  }, [notifyDescription]);

  return (
    <p
      id={descriptionId}
      data-slot="dialog-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  );
}

export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
};
