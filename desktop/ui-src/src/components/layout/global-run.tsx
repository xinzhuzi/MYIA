import { useCallback, useEffect, useState } from "react";
import { Loader2, Play, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, onSidecarEvent, SidecarRequestError } from "@/lib/api";
import type { UnlistenFn } from "@/lib/api";

/**
 * 顶栏全局「跑一次」+ 取消(C12,10-03-v112-desktop-parity;取消通道 = C2 run.cancel)。
 *
 * 品类来源 = 顶栏品类选择态(feed-ux C8 已提升到 AppLayout,经 TopBar 透传):
 * 选中品类 → health().plugins 按 id 定位其 YAML;「全部品类」→ 第一个可加载
 * 插件(与情报流空态 CTA 同口径)。run 单飞(run_busy):忙碌期按钮转
 * 「采集中 ✕」,✕ 调 run.cancel(进程组杀);completed 事件收尾回 idle
 * (各屏自订 completed 刷新,本组件不负责任何屏的数据重载)。
 *
 * 与 feed-ux G4(仪表盘品类卡跑一次)不重复:本组件只在顶栏(design §2.2)。
 */
type GlobalRunState =
  | { phase: "idle" }
  | { phase: "starting" }
  | { phase: "collecting"; runId: number }
  | { phase: "error"; message: string };

export function GlobalRun({ category }: { category: string | null }) {
  const [state, setState] = useState<GlobalRunState>({ phase: "idle" });

  const startRun = useCallback(async () => {
    setState({ phase: "starting" });
    try {
      const health = await api.health();
      const plugin = category
        ? health.plugins.find((candidate) => candidate.id === category && candidate.loaded) ??
          health.plugins.find((candidate) => candidate.id === category)
        : health.plugins.find((candidate) => candidate.loaded) ?? health.plugins[0];
      if (!plugin) {
        setState({
          phase: "error",
          message: "插件目录为空:重启应用触发首跑初始化,或到「源管理」检查插件目录。",
        });
        return;
      }
      const started = await api.runStart({ yaml: plugin.file });
      setState({ phase: "collecting", runId: started.run_id });
    } catch (err) {
      setState({
        phase: "error",
        message: err instanceof SidecarRequestError ? `${err.code}: ${err.message}` : String(err),
      });
    }
  }, [category]);

  /** ✕ 取消进行中 run;取消请求受理即回 idle(终态经 completed/run.status 可见) */
  const cancelRun = useCallback(async () => {
    if (state.phase !== "collecting") return;
    const runId = state.runId;
    try {
      await api.runCancel({ run_id: runId });
    } catch (err) {
      // run_not_active = 已终态(取消慢了一步);其余错误如实入态
      if (!(err instanceof SidecarRequestError && err.code === "run_not_active")) {
        setState({
          phase: "error",
          message: err instanceof SidecarRequestError ? `${err.code}: ${err.message}` : String(err),
        });
        return;
      }
    }
    setState({ phase: "idle" });
  }, [state]);

  // completed 事件收尾:对应 runId 结束(含 cancelled 终态)→ 回 idle
  useEffect(() => {
    if (state.phase !== "collecting") return;
    let unlisten: UnlistenFn | null = null;
    let cancelled = false;
    void onSidecarEvent((event) => {
      if (event.type === "completed" && event.run_id === state.runId) {
        setState({ phase: "idle" });
      }
    }).then((un) => {
      if (cancelled) un();
      else unlisten = un;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [state]);

  if (state.phase === "collecting") {
    return (
      <div className="flex items-center gap-1" data-testid="global-run-collecting">
        <Badge variant="default" className="gap-1 px-2 py-1" title={`run #${state.runId} 进行中`}>
          <Loader2 className="size-3 animate-spin" />
          采集中
        </Badge>
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          aria-label="取消采集"
          title="取消进行中的 run(run.cancel;等待完成请勿关闭应用)"
          onClick={() => void cancelRun()}
        >
          <X className="size-3.5" />
        </Button>
      </div>
    );
  }

  const busy = state.phase === "starting";
  return (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="sm"
        className="h-7 gap-1 px-2"
        aria-label={category ? `跑一次:${category}` : "跑一次(第一个可用品类)"}
        title={
          category
            ? `手动触发品类「${category}」采集一次(run.start)`
            : "手动触发第一个可用品类采集一次(run.start);选中品类后按品类跑"
        }
        disabled={busy}
        onClick={() => void startRun()}
      >
        {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
        {busy ? "启动中…" : "跑一次"}
      </Button>
      {state.phase === "error" ? (
        <span
          className="max-w-48 truncate text-2xs text-destructive"
          data-testid="global-run-error"
          title={state.message}
        >
          {state.message}
        </span>
      ) : null}
    </div>
  );
}
