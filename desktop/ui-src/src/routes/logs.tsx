import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";

/**
 * 采集日志(骨架):实时终端(Crawlab 式 run 瀑布)。
 * 数据流:run.start → sidecar://event 的 log/progress/completed 事件流式上屏;
 * logs.tail 补历史。客户端已具备(onSidecarEvent),上屏逻辑 C 阶段接入。
 */
export function LogsPage() {
  return (
    <div className="flex flex-col gap-4 pb-6">
      <PageHeader
        title="采集日志"
        description="实时终端:run 瀑布日志、耗时与错误高亮(stderr 通道)"
      />

      <div className="px-6">
        <div className="flex h-96 flex-col overflow-hidden rounded-lg border border-border bg-[#070b14]">
          <div className="flex items-center gap-2 border-b border-border px-3 py-1.5">
            <span className="size-2 rounded-full bg-dead/70" />
            <span className="size-2 rounded-full bg-warning/70" />
            <span className="size-2 rounded-full bg-ok/70" />
            <span className="ml-2 font-mono text-[11px] text-muted-foreground">
              sidecar://event — log / progress / completed
            </span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3 font-mono text-xs leading-relaxed">
            <p className="text-muted-foreground">
              <span className="text-brand-from">▍</span> 等待实时日志流…
            </p>
            <p className="mt-1 text-muted-foreground/70">
              # 触发一次采集(run.start)后,log / progress / completed 事件将逐行流至此终端
            </p>
            <p className="text-muted-foreground/70">
              # 历史日志可经 logs.tail 回看(环形缓冲,最近 4000 行)
            </p>
          </div>
        </div>
        <div className="mt-3">
          <EmptyState
            compact
            tag="C 阶段接入"
            title="终端交互未接入"
            description="触发 run、按 run_id 过滤与错误高亮由 C 阶段实现"
          />
        </div>
      </div>
    </div>
  );
}
