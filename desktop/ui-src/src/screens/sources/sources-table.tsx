import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ChevronDown, ChevronUp, ChevronsUpDown } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { HealthBadge } from "./health-badge";
import { ToggleSwitch } from "./toggle-switch";
import type { SourceRow } from "./api";
import type { ColumnDef, SortingState } from "@tanstack/react-table";

/** 健康度过滤档(全部 = 不筛);排序用严重度秩:ok 最轻、dead 最重 */
const HEALTH_RANK = { ok: 0, degraded: 1, dead: 2, unknown: 3 } as const;

export type HealthFilter = "all" | "ok" | "degraded" | "dead";

export const HEALTH_FILTERS: { value: HealthFilter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "ok", label: "正常" },
  { value: "degraded", label: "退化" },
  { value: "dead", label: "失效" },
];

/** 停用键:品类文件 + 源名唯一确定一个启停对象 */
export function sourceKey(file: string, name: string): string {
  return `${file}::${name}`;
}

interface SourcesTableProps {
  rows: SourceRow[];
  /** 本次会话内已停用的源(write 应答 disabled 名单) */
  disabledKeys: Set<string>;
  /** 写回/复核进行中的源(开关禁点防抖) */
  pendingKeys: Set<string>;
  onToggle: (row: SourceRow, next: boolean) => void;
}

/** 品类插件列的排序/筛选键(名称优先,缺位回退 id/文件) */
function pluginLabel(row: SourceRow): string {
  return row.pluginName ?? row.pluginId ?? row.pluginFile;
}

const COLUMNS: ColumnDef<SourceRow>[] = [
  {
    id: "sourceName",
    accessorKey: "sourceName",
    header: "源名称",
    cell: (info) => <span className="font-medium text-foreground">{info.getValue<string>()}</span>,
  },
  {
    id: "plugin",
    accessorFn: (row) => pluginLabel(row),
    header: "品类",
    cell: ({ row }) => (
      <div className="flex min-w-0 flex-col gap-0.5" title={row.original.pluginFile}>
        <span className="truncate">{pluginLabel(row.original)}</span>
        <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
          <span className="truncate font-mono">{row.original.pluginFile}</span>
          {!row.original.pluginLoaded ? <Badge variant="warning">加载失败</Badge> : null}
        </span>
      </div>
    ),
  },
  {
    id: "url",
    accessorKey: "url",
    header: "URL",
    cell: (info) => (
      <span className="block max-w-56 truncate font-mono text-xs text-muted-foreground" title={info.getValue<string>()}>
        {info.getValue<string>()}
      </span>
    ),
  },
  {
    id: "engine",
    accessorKey: "engine",
    header: "引擎",
    cell: ({ row }) => (
      <span className="font-mono text-xs" title={row.original.engineHint ?? undefined}>
        {row.original.engine}
      </span>
    ),
  },
  {
    id: "health",
    accessorFn: (row) => HEALTH_RANK[row.health.state],
    // 健康度列按严重度秩精确匹配(过滤值 = chips 选中档的秩)
    filterFn: (row, columnId, filterValue) => row.getValue<number>(columnId) === filterValue,
    header: "健康度",
    cell: ({ row }) => (
      <div className="flex flex-col gap-0.5">
        <HealthBadge state={row.original.health.state} reason={row.original.health.reason} />
        {row.original.health.state === "degraded" || row.original.health.state === "dead" ? (
          <span className="max-w-40 truncate text-[11px] text-muted-foreground" title={row.original.health.reason}>
            {row.original.health.reason}
          </span>
        ) : null}
      </div>
    ),
  },
  {
    id: "observed",
    accessorFn: (row) => row.health.observed,
    header: "最近产出",
    cell: ({ row }) => {
      const health = row.original.health;
      const latest = health.latest;
      return (
        <div className="flex flex-col gap-0.5 text-xs">
          <span className="text-foreground">
            {health.observed} 次
            {health.baseline !== null ? (
              <span className="text-muted-foreground"> · 均值 {health.baseline}</span>
            ) : null}
          </span>
          {latest ? (
            <span className={cn("text-[11px]", latest.failed ? "text-destructive" : "text-muted-foreground")}>
              {latest.failed
                ? `run#${latest.run_id} 失败${latest.skip_reason ? `:${latest.skip_reason}` : ""}`
                : `run#${latest.run_id} ${latest.item_count} 条`}
            </span>
          ) : (
            <span className="text-[11px] text-muted-foreground">暂无运行记录</span>
          )}
        </div>
      );
    },
  },
  {
    id: "toggle",
    header: "启停",
    enableSorting: false,
    enableGlobalFilter: false,
    cell: ({ row, table }) => {
      const key = sourceKey(row.original.pluginFile, row.original.sourceName);
      const meta = table.options.meta as SourcesTableMeta;
      const enabled = !meta.disabledKeys.has(key);
      return (
        <ToggleSwitch
          checked={enabled}
          disabled={meta.pendingKeys.has(key)}
          ariaLabel={`${enabled ? "停用" : "启用"} ${row.original.sourceName}`}
          onCheckedChange={(next) => meta.onToggle(row.original, next)}
        />
      );
    },
  },
];

/** 传给单元格的回调集合(TanStack table.options.meta 惯例) */
interface SourcesTableMeta {
  disabledKeys: Set<string>;
  pendingKeys: Set<string>;
  onToggle: (row: SourceRow, next: boolean) => void;
}

/**
 * 插件/源表格(TanStack Table:排序/筛选/分页;shadcn 暗色样式)。
 * 列头点击循环排序(升→降→取消);全局文本框与健康度 chips 由父组件受控传入。
 */
export function SourcesTable({ rows, disabledKeys, pendingKeys, onToggle }: SourcesTableProps) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");
  const [healthFilter, setHealthFilter] = useState<HealthFilter>("all");

  const columns = useMemo(() => COLUMNS, []);
  const columnFilters = useMemo(
    () => (healthFilter === "all" ? [] : [{ id: "health", value: HEALTH_RANK[healthFilter] }]),
    [healthFilter],
  );

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting, globalFilter, columnFilters },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 10 } },
    globalFilterFn: "includesString",
    meta: { disabledKeys, pendingKeys, onToggle } satisfies SourcesTableMeta,
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <input
          value={globalFilter}
          onChange={(event) => setGlobalFilter(event.target.value)}
          placeholder="筛选:源名 / URL / 品类…"
          aria-label="全局筛选"
          className="h-8 w-64 rounded-md border border-input bg-transparent px-2.5 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-[3px] focus-visible:ring-ring/40"
        />
        <div className="flex items-center gap-1" role="group" aria-label="健康度筛选">
          {HEALTH_FILTERS.map(({ value, label }) => (
            <Button
              key={value}
              size="sm"
              variant={healthFilter === value ? "secondary" : "ghost"}
              onClick={() => setHealthFilter(value)}
            >
              {label}
            </Button>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/40 text-xs text-muted-foreground">
              {table.getFlatHeaders().map((header) => {
                const canSort = header.column.getCanSort();
                const direction = header.column.getIsSorted();
                return (
                  <th
                    key={header.id}
                    className={cn("px-4 py-2 text-left font-medium", canSort && "cursor-pointer select-none hover:text-foreground")}
                    onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                    aria-sort={direction === "asc" ? "ascending" : direction === "desc" ? "descending" : undefined}
                  >
                    <span className="flex items-center gap-1">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {canSort ? (
                        direction === "asc" ? (
                          <ChevronUp className="size-3" />
                        ) : direction === "desc" ? (
                          <ChevronDown className="size-3" />
                        ) : (
                          <ChevronsUpDown className="size-3 opacity-50" />
                        )
                      ) : null}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id} className="border-b border-border/50 transition-colors hover:bg-muted/30">
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-4 py-2.5 align-middle">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          共 {table.getFilteredRowModel().rows.length} 行
          {table.getFilteredRowModel().rows.length !== rows.length ? `(全部 ${rows.length} 行)` : ""}
        </span>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" disabled={!table.getCanPreviousPage()} onClick={table.previousPage}>
            上一页
          </Button>
          <span>
            第 {table.getState().pagination.pageIndex + 1} / {Math.max(table.getPageCount(), 1)} 页
          </span>
          <Button variant="outline" size="sm" disabled={!table.getCanNextPage()} onClick={table.nextPage}>
            下一页
          </Button>
        </div>
      </div>
    </div>
  );
}
