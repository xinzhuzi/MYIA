import { FilePlus2, Trash2, X } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { isValidFileStem } from "./api";
import type { YamlFileEntry } from "./api";

interface FileListProps {
  files: YamlFileEntry[];
  /** 当前选中文件(绝对路径;草稿态 = 草稿目标路径) */
  selectedFile: string | null;
  /** 未保存草稿的文件名(列表顶部伪条目,带 * 与「未保存」徽标) */
  draftName: string | null;
  onSelect: (file: string) => void;
  onDelete: (entry: YamlFileEntry) => void;
  /** stem 已过预检;模板取回与草稿建立由屏层负责 */
  onCreate: (stem: string) => void;
}

/**
 * 左栏文件列表:品类名(坏文件回退文件名)+ 损坏徽标 + 选中态 + hover 删除;
 * 列表头「新建」(stem 正则预检失败即拦,不发请求)。
 * 空目录 = 合法空态(用户删光全部品类):EmptyState + 「新建第一个品类」CTA,
 * 与 feed 空态引导同款闭环,不是死胡同。
 */
export function FileList({ files, selectedFile, draftName, onSelect, onDelete, onCreate }: FileListProps) {
  const [creating, setCreating] = useState(false);

  return (
    <div className="flex h-full min-h-0 flex-col gap-2" data-testid="yaml-file-list">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium text-foreground">品类文件({files.length})</p>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setCreating((prev) => !prev)}
          aria-expanded={creating}
        >
          <FilePlus2 className="size-3.5" />
          新建
        </Button>
      </div>

      {creating ? (
        <CreateFileForm
          onCancel={() => setCreating(false)}
          onSubmit={(stem) => {
            setCreating(false);
            onCreate(stem);
          }}
        />
      ) : null}

      {files.length === 0 && draftName === null ? (
        <EmptyState
          compact
          title="还没有品类文件"
          description="目录里没有 *.yaml;「新建」一个,或删除全部后从模板重建。"
          action={
            <Button size="sm" variant="outline" onClick={() => setCreating(true)}>
              <FilePlus2 className="size-3.5" />
              新建第一个品类
            </Button>
          }
        />
      ) : (
        <ul className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto" aria-label="品类文件清单">
          {draftName !== null ? (
            <li
              className="rounded-md border border-primary/40 bg-primary/10 px-2.5 py-2"
              data-testid="draft-entry"
            >
              <span className="flex items-center justify-between gap-2">
                <span className="truncate text-[13px] font-medium text-foreground">
                  {draftName} *
                </span>
                <Badge variant="default">未保存</Badge>
              </span>
            </li>
          ) : null}
          {files.map((entry) => {
            const selected = entry.file === selectedFile;
            return (
              <li key={entry.file} className="group relative">
                <button
                  type="button"
                  onClick={() => onSelect(entry.file)}
                  aria-current={selected ? "true" : undefined}
                  className={cn(
                    "flex w-full flex-col gap-0.5 rounded-md px-2.5 py-2 pr-8 text-left transition-colors",
                    selected ? "bg-accent" : "hover:bg-accent/60",
                  )}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span
                      className={cn(
                        "truncate text-[13px]",
                        selected ? "font-medium text-foreground" : "text-foreground",
                      )}
                    >
                      {entry.category_name ?? entry.name}
                    </span>
                    {!entry.parse_ok ? <Badge variant="destructive">损坏</Badge> : null}
                  </span>
                  <span className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                    <span className="truncate font-mono" title={entry.file}>
                      {entry.name}
                    </span>
                    {entry.parse_ok ? (
                      <span className="shrink-0">{entry.sources} 源</span>
                    ) : (
                      <span className="shrink-0 truncate" title={entry.error?.message ?? undefined}>
                        {entry.error?.code}
                      </span>
                    )}
                  </span>
                </button>
                <button
                  type="button"
                  aria-label={`删除 ${entry.name}`}
                  title="删除(先 .bak 留底)"
                  onClick={() => onDelete(entry)}
                  className="absolute top-1/2 right-1.5 hidden -translate-y-1/2 rounded-md p-1 text-muted-foreground transition-colors hover:bg-destructive/20 hover:text-destructive group-hover:block focus-visible:block"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

interface CreateFileFormProps {
  onSubmit: (stem: string) => void;
  onCancel: () => void;
}

/** 新建输入:stem 前端预检(同源正则 CATEGORY_ID_PATTERN),失败内联报因不发请求。 */
function CreateFileForm({ onSubmit, onCancel }: CreateFileFormProps) {
  const [stem, setStem] = useState("");
  const trimmed = stem.trim();
  const invalid = trimmed.length > 0 && !isValidFileStem(trimmed);

  return (
    <form
      className="flex flex-col gap-1.5 rounded-md border border-border bg-muted/30 p-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (!trimmed || invalid) return; // 预检失败即拦:不建草稿不发请求
        onSubmit(trimmed);
      }}
    >
      <input
        autoFocus
        value={stem}
        aria-label="新建文件名"
        placeholder="小写字母/数字/-/_,1-64 字符(如 ai-news)"
        onChange={(event) => setStem(event.target.value)}
        className={cn(
          "h-8 rounded-md border border-input bg-transparent px-2.5 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-[3px] focus-visible:ring-ring/40",
          invalid && "border-destructive",
        )}
      />
      {invalid ? (
        <p role="alert" className="text-[11px] text-destructive">
          文件名不合规:只允许小写字母/数字/-/_,1-64 字符;中文名请写进 name: 字段
        </p>
      ) : null}
      <span className="flex items-center gap-1.5">
        <Button type="submit" size="sm" disabled={!trimmed || invalid}>
          创建草稿
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel} aria-label="取消新建">
          <X className="size-3.5" />
          取消
        </Button>
      </span>
    </form>
  );
}
