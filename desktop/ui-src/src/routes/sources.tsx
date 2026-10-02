import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

/** 源管理表格列(TanStack 表格列头先立,C 阶段接排序/筛选/分页) */
const TABLE_COLUMNS = ["名称", "URL", "引擎", "健康度", "最近产出"] as const;

/**
 * 源管理(骨架):增删改查写回品类 YAML(往返一致是 v1.1 验收项)。
 * TanStack 表格与 YAML 写回(C 阶段,经侧写文件协议扩展)。
 */
export function SourcesPage() {
  return (
    <div className="flex flex-col gap-4 pb-6">
      <PageHeader
        title="源管理"
        description="品类源的增删改查;改动写回品类 YAML 并被 myia run 识别"
        actions={
          <Button size="sm" disabled>
            新增源
          </Button>
        }
      />

      <div className="px-6">
        <Card>
          <CardContent className="p-0">
            <div className="grid grid-cols-[1.2fr_2fr_0.8fr_0.8fr_1fr] gap-2 border-b border-border bg-muted/40 px-4 py-2 text-xs text-muted-foreground">
              {TABLE_COLUMNS.map((column) => (
                <span key={column}>{column}</span>
              ))}
            </div>
            <EmptyState
              tag="C 阶段接入"
              title="还没有源数据"
              description="表格(排序/筛选/分页/列宽)与 YAML 写回由 C 阶段实现;当前清单可经 api.health() 只读预览"
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
