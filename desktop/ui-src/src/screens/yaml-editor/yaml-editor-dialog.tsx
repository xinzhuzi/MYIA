import { Save, ShieldCheck, X } from "lucide-react";
import { useCallback, useEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { ErrorBox } from "./error-box";
import { EditorPane } from "./editor-pane";
import { FindingsPanel } from "./findings-panel";
import { fileNameOf, useYamlFileEditor } from "./use-yaml-file-editor";

interface YamlEditorDialogProps {
  /** 要编辑的品类 YAML 绝对路径(源管理行 pluginFile 口径) */
  file: string;
  /** 关闭弹窗(dirty 时组件内部先 confirm,确认放弃才回调) */
  onClose: () => void;
  /** 保存成功回调(源管理屏:刷新表格,防编辑后展示陈旧行;缺省不刷新) */
  onSaved?: () => void;
}

/**
 * 源管理屏的当场编辑弹窗:不离开源管理屏,弹出模态对话框编辑既有品类文件。
 *
 * 编辑内核 = use-yaml-file-editor(与双栏配置编辑屏共用同一份保存逻辑:
 * 同门校验零写入 → mtime 乐观锁 → .bak → 原子落盘 → doctor 复核)。
 * 「跑一次/新建/删除」不进弹窗(留在配置编辑屏;弹窗 = 快速编辑既有文件)。
 * 零新 npm 依赖:手写 overlay(fixed 遮罩 + 居中面板,role=dialog aria-modal);
 * ESC / 遮罩点击 / 关闭按钮都走同一守卫:dirty 时先 window.confirm。
 * mtime_conflict 走 ErrorBox 惯例并给「重读」(不覆盖外部改动)。
 */
export function YamlEditorDialog({ file, onClose, onSaved }: YamlEditorDialogProps) {
  const {
    doc,
    docReady,
    dirty,
    validate,
    save,
    runNotice,
    openFile,
    setContent,
    runValidate,
    saveFile,
    confirmDiscardIfDirty,
  } = useYamlFileEditor({ onSaved });

  const panelRef = useRef<HTMLDivElement>(null);

  // 打开即读原文;file 只应用一次(openFile 引用随 dirty 守卫变化,防重入)
  const openedFile = useRef<string | null>(null);
  useEffect(() => {
    if (openedFile.current === file) return;
    openedFile.current = file;
    void openFile(file);
  }, [file, openFile]);

  /** 关闭守卫:dirty 先 confirm(与屏内切文件守卫同款文案),确认放弃才 onClose */
  const attemptClose = useCallback(() => {
    if (!confirmDiscardIfDirty()) return;
    onClose();
  }, [confirmDiscardIfDirty, onClose]);

  // 键盘:ESC 关闭(同守卫);Meta+S 保存(preventDefault 防 webview 默认行为)
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        attemptClose();
      } else if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void saveFile();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [attemptClose, saveFile]);

  // 模态基础焦点:面板挂载即聚焦(ESC 无需先点进编辑器;轮廓不抢视觉)
  useEffect(() => {
    panelRef.current?.focus();
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 animate-overlay-in"
      onClick={attemptClose}
      data-testid="yaml-editor-overlay"
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={`编辑 ${fileNameOf(file)}`}
        tabIndex={-1}
        data-testid="yaml-editor-dialog"
        className="flex h-[75vh] w-[80vw] flex-col gap-2 rounded-lg border border-border bg-card p-4 shadow-drawer animate-dialog-in outline-none"
        onClick={(event) => event.stopPropagation()}
      >
        {/* 标题区:文件名 + dirty 标记 + 完整路径小字 + 关闭 */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 flex-col gap-0.5" data-testid="editor-title">
            <span className="truncate text-sm font-medium text-foreground">
              {docReady ? docReady.fileName : fileNameOf(file)}
              {dirty ? " *" : ""}
            </span>
            <span
              className="max-w-[70vw] truncate font-mono text-2xs text-muted-foreground"
              title={file}
              data-testid="editor-path"
            >
              {file}
            </span>
          </div>
          <Button size="sm" variant="ghost" aria-label="关闭对话框" onClick={attemptClose}>
            <X className="size-4" />
          </Button>
        </div>

        {/* 编辑器主体 */}
        <div className="min-h-0 flex-1">
          {doc.status === "loading" ? (
            <div className="flex h-full flex-col gap-2" aria-label="文件读取中">
              <Skeleton className="h-full w-full" />
            </div>
          ) : doc.status === "error" ? (
            <ErrorBox error={doc.error} onRetry={() => void openFile(file)} />
          ) : doc.status === "idle" ? null : (
            <EditorPane value={doc.content} onChange={setContent} className="h-full" />
          )}
        </div>

        {/* 结果区:校验 findings / 保存结构化错误(mtime_conflict 给重读)/ doctor 复核 */}
        <div className="flex max-h-40 shrink-0 flex-col gap-1.5 overflow-y-auto border-t border-border pt-2">
          {validate.error ? <ErrorBox error={validate.error} /> : null}
          {validate.result ? (
            <FindingsPanel
              findings={validate.result.findings}
              emptyText={
                validate.result.valid
                  ? validate.result.category
                    ? `校验通过:${validate.result.category.name}(${validate.result.category.sources} 源)`
                    : "校验通过"
                  : null
              }
            />
          ) : null}

          {save.error ? (
            <ErrorBox
              error={save.error}
              onRetry={save.error.code === "mtime_conflict" ? () => void openFile(file) : undefined}
              retryLabel="重读"
            />
          ) : null}
          {save.created !== null && save.error === null ? (
            <p role="status" data-testid="save-ok" className="text-xs text-ok">
              已保存 · mtime 基线已更新
            </p>
          ) : null}
          {save.created !== null && save.error === null ? (
            <FindingsPanel findings={save.warnings} emptyText="保存完成,无警告" />
          ) : null}
          {save.doctor ? (
            <p
              role="status"
              data-testid="doctor-check"
              className={save.doctor.ok ? "text-xs text-ok" : "text-xs text-destructive"}
            >
              {save.doctor.message}
            </p>
          ) : null}
          {runNotice ? (
            <span role="status" data-testid="run-notice" className="text-2xs text-warning">
              {runNotice}
            </span>
          ) : null}
        </div>

        {/* 底部动作条:校验 / 保存(Meta+S)/ 关闭 */}
        <div className="flex shrink-0 items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={docReady === null || validate.running}
            onClick={() => void runValidate()}
          >
            <ShieldCheck className="size-3.5" />
            {validate.running ? "校验中…" : "校验"}
          </Button>
          <Button size="sm" disabled={docReady === null || !dirty || save.saving} onClick={() => void saveFile()}>
            <Save className="size-3.5" />
            {save.saving ? "保存中…" : "保存"}
          </Button>
          <span className="text-2xs text-muted-foreground">⌘S 保存</span>
          <span className="flex-1" />
          <Button size="sm" variant="outline" onClick={attemptClose}>
            关闭
          </Button>
        </div>
      </div>
    </div>
  );
}
