import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * shadcn/ui tabs API,无 Radix 依赖(任务红线不装包):
 * context + role=tablist/tab/tabpanel,方向键/Home/End 巡游(自动激活),
 * roving tabindex;内容切换仅 120ms 淡入(Linear:只动 opacity/transform)。
 * 分段控制器样式与 button/select 基件同族(muted 轨道 + secondary 激活段)。
 */

type TabsContextValue = {
  value: string;
  setValue: (value: string) => void;
  baseId: string;
  triggersRef: React.RefObject<Map<string, HTMLButtonElement>>;
};

const TabsContext = React.createContext<TabsContextValue | null>(null);

function useTabsContext(component: string) {
  const ctx = React.useContext(TabsContext);
  if (!ctx) throw new Error(`<${component}> 必须在 <Tabs> 内使用`);
  return ctx;
}

function Tabs({
  value: valueProp,
  defaultValue,
  onValueChange,
  className,
  children,
  ...props
}: React.ComponentProps<"div"> & {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
}) {
  const [internal, setInternal] = React.useState(defaultValue ?? "");
  const isControlled = valueProp !== undefined;
  const value = isControlled ? valueProp : internal;
  const baseId = React.useId();
  const triggersRef = React.useRef<Map<string, HTMLButtonElement>>(new Map());

  const setValue = React.useCallback(
    (next: string) => {
      if (!isControlled) setInternal(next);
      onValueChange?.(next);
    },
    [isControlled, onValueChange],
  );

  const ctx = React.useMemo(
    () => ({ value, setValue, baseId, triggersRef }),
    [value, setValue, baseId],
  );

  return (
    <TabsContext.Provider value={ctx}>
      <div data-slot="tabs" className={className} {...props}>
        {children}
      </div>
    </TabsContext.Provider>
  );
}

function TabsList({ className, onKeyDown, ...props }: React.ComponentProps<"div">) {
  const { triggersRef, setValue } = useTabsContext("TabsList");

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const keys = [...triggersRef.current.values()]
      .filter((el) => !el.disabled)
      .map((el) => el.getAttribute("data-value") ?? "")
      .filter(Boolean);
    if (keys.length === 0) return;

    const current = [...triggersRef.current.entries()].find(
      ([, el]) => el === document.activeElement,
    )?.[0];
    const index = current ? keys.indexOf(current) : -1;
    let next: string | undefined;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      next = keys[(index + 1 + keys.length) % keys.length];
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      next = keys[(index - 1 + keys.length) % keys.length];
    } else if (event.key === "Home") {
      next = keys[0];
    } else if (event.key === "End") {
      next = keys[keys.length - 1];
    }
    if (next === undefined) return;
    event.preventDefault();
    triggersRef.current.get(next)?.focus();
    setValue(next);
    onKeyDown?.(event);
  }

  return (
    <div
      role="tablist"
      data-slot="tabs-list"
      onKeyDown={handleKeyDown}
      className={cn(
        "inline-flex h-8 w-fit items-center justify-center gap-0.5 rounded-md bg-muted p-[3px] text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

function TabsTrigger({
  className,
  value,
  disabled,
  ...props
}: React.ComponentProps<"button"> & { value: string }) {
  const { value: activeValue, setValue, baseId, triggersRef } = useTabsContext("TabsTrigger");
  const isActive = activeValue === value;

  return (
    <button
      type="button"
      role="tab"
      data-value={value}
      ref={(el) => {
        if (el) triggersRef.current.set(value, el);
        else triggersRef.current.delete(value);
      }}
      id={`${baseId}-trigger-${value}`}
      aria-selected={isActive}
      aria-controls={`${baseId}-content-${value}`}
      tabIndex={isActive ? 0 : -1}
      data-state={isActive ? "active" : "inactive"}
      data-disabled={disabled ? "" : undefined}
      disabled={disabled}
      onClick={() => setValue(value)}
      data-slot="tabs-trigger"
      className={cn(
        "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-sm px-2.5 text-xs font-medium",
        "transition-colors duration-(--duration-fast) ease-out-expo",
        "data-[state=active]:bg-secondary data-[state=active]:text-secondary-foreground data-[state=active]:shadow-xs",
        "data-[state=inactive]:hover:text-foreground",
        "disabled:pointer-events-none disabled:opacity-50",
        "[&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4",
        className,
      )}
      {...props}
    />
  );
}

function TabsContent({
  className,
  value,
  ...props
}: React.ComponentProps<"div"> & { value: string }) {
  const { value: activeValue, baseId } = useTabsContext("TabsContent");
  if (activeValue !== value) return null;

  return (
    <div
      role="tabpanel"
      id={`${baseId}-content-${value}`}
      aria-labelledby={`${baseId}-trigger-${value}`}
      tabIndex={0}
      data-slot="tabs-content"
      data-state="active"
      className={cn("outline-none animate-fade-in", className)}
      {...props}
    />
  );
}

export { Tabs, TabsContent, TabsList, TabsTrigger };
