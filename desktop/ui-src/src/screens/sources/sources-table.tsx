import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ChevronDown, ChevronUp, ChevronsUpDown, Loader2, FlaskConical } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
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
  /** 试抓进行中的源键(null = 空闲;单飞,job 期间全表试抓禁点) */
  testingKey: string | null;
  onToggle: (row: SourceRow, next: boolean) => void;
  /** 行「编辑」:当场弹出 YAML 编辑对话框(屏层持有 dialog 状态) */
  onEdit: (row: SourceRow) => void;
  /** 行「试抓」:发起 sources.test 异步 job(C13;结果经 test.completed 事件回屏) */
  onTest: (row: SourceRow) => void;
}

/** 品类插件列的排序/筛选键(名称优先,缺位回退 id/文件) */
function pluginLabel(row: SourceRow): string {
  return row.pluginName ?? row.pluginId ?? row.pluginFile;
}

/** 最近产出列的单行摘要 title(完整 run 明细收进悬浮提示,compact 行不折行) */
function observedTitle(row: SourceRow): string {
  const latest = row.health.latest;
  if (!latest) return `${row.health.observed} 次 · 暂无运行记录`;
  return latest.failed
    ? `${row.health.observed} 次 · run#${latest.run_id} 失败${latest.skip_reason ? `:${latest.skip_reason}` : ""}`
    : `${row.health.observed} 次 · run#${latest.run_id} ${latest.item_count} 条`;
}

/**
 * 列定义:单行紧凑单元格(compact 36px 行密度,细节走 title 悬浮提示)。
 * size/minSize = 列宽拖拽的初始值与下限(Ant Table 手感:拖右缘手柄实时改宽)。
 */
const COLUMNS: ColumnDef<SourceRow>[] = [
  {
    id: "sourceName",
    accessorKey: "sourceName",
    header: "源名称",
    size: 150,
    minSize: 96,
    cell: (info) => <span className="block truncate font-medium text-foreground">{info.getValue<string>()}</span>,
  },
  {
    id: "plugin",
    accessorFn: (row) => pluginLabel(row),
    header: "品类",
    size: 240,
    minSize: 150,
    cell: ({ row }) => (
      <div className="flex min-w-0 items-center gap-1.5" title={row.original.pluginFile}>
        <span className="min-w-0 truncate text-foreground">{pluginLabel(row.original)}</span>
        <span className="shrink-0 truncate font-mono text-2xs text-muted-foreground">
          {row.original.pluginFile}
        </span>
        {!row.original.pluginLoaded ? <Badge variant="warning">加载失败</Badge> : null}
      </div>
    ),
  },
  {
    id: "url",
    accessorKey: "url",
    header: "URL",
    size: 280,
    minSize: 120,
    cell: (info) => (
      <span className="block max-w-full truncate font-mono text-xs text-muted-foreground" title={info.getValue<string>()}>
        {info.getValue<string>()}
      </span>
    ),
  },
  {
    id: "engine",
    accessorKey: "engine",
    header: "引擎",
    size: 100,
    minSize: 76,
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
    size: 96,
    minSize: 80,
    cell: ({ row }) => (
      <HealthBadge state={row.original.health.state} reason={row.original.health.reason} />
    ),
  },
  {
    id: "observed",
    accessorFn: (row) => row.health.observed,
    header: "最近产出",
    size: 210,
    minSize: 130,
    cell: ({ row }) => {
      const health = row.original.health;
      const latest = health.latest;
      return (
        <span className="block truncate text-xs" title={observedTitle(row.original)}>
          <span className="text-foreground">{health.observed} 次</span>
          {health.baseline !== null ? (
            <span className="text-muted-foreground"> · 均值 {health.baseline}</span>
          ) : null}
          {latest ? (
            latest.failed ? (
              <span className="text-destructive"> · run#{latest.run_id} 失败</span>
            ) : (
              <span className="text-muted-foreground"> · run#{latest.run_id} {latest.item_count} 条</span>
            )
          ) : (
            <span className="text-muted-foreground"> · 暂无运行记录</span>
          )}
        </span>
      );
    },
  },
  {
    id: "toggle",
    header: "启停",
    enableSorting: false,
    enableGlobalFilter: false,
    enableResizing: false,
    size: 64,
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
  {
    id: "actions",
    header: "操作",
    enableSorting: false,
    enableGlobalFilter: false,
    enableResizing: false,
    cell: ({ row, table }) => {
      const meta = table.options.meta as SourcesTableMeta;
      const key = sourceKey(row.original.pluginFile, row.original.sourceName);
      const testing = meta.testingKey === key;
      return (
        <div className="flex items-center gap-1">
          {/* 试抓动作(C13):异步 job(sources.test),结果在表格上方回显;单飞期间全表禁点 */}
          <Button
            size="sm"
            variant="outline"
            disabled={meta.testingKey !== null}
            aria-label={`试抓 ${row.original.sourceName}`}
            title={`myia test ${row.original.pluginFile} --source ${row.original.sourceName}`}
            onClick={() => meta.onTest(row.original)}
          >
            {testing ? <Loader2 className="size-3.5 animate-spin" /> : <FlaskConical className="size-3.5" />}
            试抓
          </Button>
          {/* 编辑动作:当场弹出编辑对话框(不离开源管理屏;深链 /yaml-editor?file= 仍可用) */}
          <Button
            size="sm"
            variant="outline"
            title={`弹出编辑对话框:${row.original.pluginFile}`}
            onClick={() => meta.onEdit(row.original)}
          >
            编辑
          </Button>
        </div>
      );
    },
  },
];

/** 传给单元格的回调集合(TanStack table.options.meta 惯例) */
interface SourcesTableMeta {
  disabledKeys: Set<string>;
  pendingKeys: Set<string>;
  /** 试抓进行中的源键(null = 空闲;单飞,job 期间全表试抓禁点) */
  testingKey: string | null;
  onToggle: (row: SourceRow, next: boolean) => void;
  onEdit: (row: SourceRow) => void;
  /** 行「试抓」:发起 sources.test 异步 job(结果经 test.completed 事件回屏) */
  onTest: (row: SourceRow) => void;
}

/**
 * 插件/源表格:引擎仍是 TanStack Table(排序/筛选/分页),渲染层迁 Phase1
 * 基件 ui/table(D4):compact 36px 行密度、细边框分层、表头 2xs;列宽拖拽
 * (columnResizeMode=onChange,拖右缘手柄实时改宽,双击手柄复位);末列(操作)
 * 不定宽,吃掉剩余宽度 —— 其余列渲染宽度 = getSize() 像素原值,拖拽所见即所得。
 * 列头点击循环排序(升→降→取消);全局文本框与健康度 chips 由父组件受控传入。
 */
export function SourcesTable({
  rows,
  disabledKeys,
  pendingKeys,
  testingKey,
  onToggle,
  onEdit,
  onTest,
}: SourcesTableProps) {
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
    columnResizeMode: "onChange",
    meta: { disabledKeys, pendingKeys, testingKey, onToggle, onEdit, onTest } satisfies SourcesTableMeta,
  });

  // v8 字段为 isResizingColumn(false | string;v9 才更名 isResizingActive):非 false = 拖拽中
  const resizing = table.getState().columnSizingInfo.isResizingColumn !== false;
  const leafColumns = table.getVisibleLeafColumns();
  const headers = table.getHeaderGroups()[0]?.headers ?? [];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Input
          value={globalFilter}
          onChange={(event) => setGlobalFilter(event.target.value)}
          placeholder="筛选:源名 / URL / 品类…"
          aria-label="全局筛选"
          className="w-64"
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

      <Table className={cn("table-fixed", resizing && "select-none")}>
        {/* 列宽拖拽:定宽列渲染 getSize() 原值;末列(操作)不定宽吃剩余宽度 */}
        <colgroup>
          {leafColumns.map((column, index) =>
            index === leafColumns.length - 1 ? (
              <col key={column.id} />
            ) : (
              <col key={column.id} style={{ width: column.getSize() }} />
            ),
          )}
        </colgroup>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            {headers.map((header, index) => {
              const canSort = header.column.getCanSort();
              const direction = header.column.getIsSorted();
              const canResize = header.column.getCanResize();
              const columnResizing = header.column.getIsResizing();
              return (
                <TableHead
                  key={header.id}
                  style={index === headers.length - 1 ? undefined : { width: header.getSize() }}
                  className={cn("relative", canSort && "select-none")}
                  aria-sort={direction === "asc" ? "ascending" : direction === "desc" ? "descending" : undefined}
                >
                  {canSort ? (
                    /* 键盘可达(P1):排序控件用原生 button —— Tab 可聚焦,Enter/Space
                       由 UA 合成 click 触发同一 handler;焦点环走全局 :focus-visible ring */
                    <button
                      type="button"
                      onClick={header.column.getToggleSortingHandler()}
                      className="flex w-full cursor-pointer items-center gap-1 text-left hover:text-foreground"
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {direction === "asc" ? (
                        <ChevronUp className="size-3" />
                      ) : direction === "desc" ? (
                        <ChevronDown className="size-3" />
                      ) : (
                        <ChevronsUpDown className="size-3 opacity-50" />
                      )}
                    </button>
                  ) : (
                    <span className="flex items-center gap-1">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </span>
                  )}
                  {canResize ? (
                    <span
                      aria-hidden
                      data-column-resize-handle={header.id}
                      onPointerDown={header.getResizeHandler()}
                      onDoubleClick={() => header.column.resetSize()}
                      onClick={(event) => event.stopPropagation()}
                      className={cn(
                        "group absolute inset-y-0 right-0 z-10 flex w-2 cursor-col-resize touch-none items-stretch justify-center",
                      )}
                    >
                      {/* Ant Table 手感:平时一缕 hairline,悬浮/拖拽中亮品牌青 */}
                      <span
                        className={cn(
                          "h-full w-px bg-border transition-colors duration-(--duration-fast) ease-out-expo",
                          "group-hover:bg-primary",
                          columnResizing && "bg-primary",
                        )}
                      />
                    </span>
                  ) : null}
                </TableHead>
              );
            })}
          </TableRow>
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => (
            <TableRow key={row.id}>
              {row.getVisibleCells().map((cell) => (
                <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>

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
