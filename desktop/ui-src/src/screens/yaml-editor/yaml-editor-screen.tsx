import { Play, RefreshCw, Save, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useBlocker, useSearchParams } from "react-router-dom";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { SidecarRequestError } from "@/lib/api";

import { ErrorBox } from "./error-box";
import { EditorPane } from "./editor-pane";
import { FileList } from "./file-list";
import { FindingsPanel } from "./findings-panel";
import { asSidecarError, deleteYaml, listYamlFiles } from "./api";
import type { YamlFileEntry } from "./api";
import { sameFilePath, useYamlFileEditor } from "./use-yaml-file-editor";

/** 列表态(loading/error/ready;坏文件也入列,交给 FileList 打损坏徽标) */
interface ListState {
  status: "loading" | "ready" | "error";
  files: YamlFileEntry[];
  pluginsDir: string;
  error: SidecarRequestError | null;
}

interface RunView {
  starting: boolean;
  runId: number | null;
  error: SidecarRequestError | null;
}

const INITIAL_RUN: RunView = { starting: false, runId: null, error: null };

/**
 * 配置编辑第六屏:左文件列表 + 右 CodeMirror 原文编辑。
 *
 * 编辑内核(读取→dirty→校验→保存→doctor 复核)在 use-yaml-file-editor,
 * 与源管理弹窗(yaml-editor-dialog.tsx)共享同一份保存逻辑。本屏独有:
 * 文件列表/新建/删除/跑一次/路由级 dirty 守卫。源管理行「编辑」当场弹窗
 * (不跳本屏);/yaml-editor 与 ?file= 深链保持可用(侧栏入口)。
 */
export function YamlEditorScreen() {
  const [list, setList] = useState<ListState>({ status: "loading", files: [], pluginsDir: "", error: null });
  const [run, setRun] = useState<RunView>(INITIAL_RUN);
  /** 删除等列表动作的结构化错误(列表整体仍可用,故不进 ListState.error) */
  const [actionError, setActionError] = useState<SidecarRequestError | null>(null);

  const reloadList = useCallback(async () => {
    setList({ status: "loading", files: [], pluginsDir: "", error: null });
    try {
      const result = await listYamlFiles();
      setList({ status: "ready", files: result.files, pluginsDir: result.plugins_dir, error: null });
    } catch (error) {
      setList({ status: "error", files: [], pluginsDir: "", error: asSidecarError(error) });
    }
  }, []);

  /** 打开/新建文件时的伴随重置(跑一次结果与列表动作错误;编辑内核态由 hook 清) */
  const resetAux = useCallback(() => {
    setRun(INITIAL_RUN);
    setActionError(null);
  }, []);

  const handleSaved = useCallback(() => {
    void reloadList();
  }, [reloadList]);

  const {
    doc,
    docReady,
    dirty,
    validate,
    save,
    runNotice,
    openFile,
    createDraft,
    setContent,
    runValidate,
    saveFile,
    checkRunInFlight,
    invalidate,
    clear,
    clearRunNotice,
  } = useYamlFileEditor({ onSaved: handleSaved, onOpenStart: resetAux });

  useEffect(() => {
    void reloadList();
  }, [reloadList]);

  /** 源管理「编辑」深链预选:?file=… 只应用一次,后续由用户选择主导 */
  const [searchParams] = useSearchParams();
  const preselectFile = searchParams.get("file");
  const appliedPreselect = useRef<string | null>(null);
  useEffect(() => {
    if (preselectFile === null || appliedPreselect.current === preselectFile) return;
    appliedPreselect.current = preselectFile;
    void openFile(preselectFile);
  }, [preselectFile, openFile]);

  /** 跑一次:复用 run.start;dirty 禁用(title 提示先保存);发起后引导去日志屏 */
  const handleRunOnce = useCallback(async () => {
    if (docReady === null || dirty || run.starting) return;
    setRun((prev) => ({ ...INITIAL_RUN, starting: true, error: prev.error }));
    clearRunNotice();
    void checkRunInFlight(docReady.file);
    try {
      const started = await api.runStart({ yaml: docReady.file });
      setRun({ starting: false, runId: started.run_id, error: null });
    } catch (error) {
      setRun({ starting: false, runId: null, error: asSidecarError(error) });
    }
  }, [checkRunInFlight, clearRunNotice, dirty, docReady, run.starting]);

  /** 删除:confirm 文案含 .bak 留底与官方件重建提示(决议 9);选中项被删回 idle */
  const handleDelete = useCallback(
    async (entry: YamlFileEntry) => {
      const label = entry.category_name ?? entry.name;
      const confirmed = window.confirm(
        `确定删除「${label}」(${entry.name})?\n\n` +
          `删除前会在同目录留底 ${entry.name}.bak;\n` +
          `官方件删除后需重装或从模板重建。`,
      );
      if (!confirmed) return;
      setActionError(null);
      try {
        await deleteYaml(entry.file);
        if (
          (doc.status === "ready" || doc.status === "loading") &&
          sameFilePath(doc.file, entry.file)
        ) {
          clear(); // 作废在途读取 + 编辑器回 idle + 清结果
          resetAux();
        } else {
          invalidate(); // 删的是别的文件:仅作废在途应答,编辑器不动
        }
        await reloadList();
      } catch (error) {
        setActionError(asSidecarError(error));
      }
    },
    [doc, invalidate, clear, reloadList, resetAux],
  );

  // Cmd+S = 保存(preventDefault 防 webview 默认行为;编辑器无快捷键等于没腿)
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void saveFile();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [saveFile]);

  // 应用级 dirty 守卫(窗口关闭/刷新)在 use-yaml-file-editor(与弹窗共用)

  // SPA 路由级 dirty 守卫:侧栏切屏等应用内导航经 useBlocker 拦截(main.tsx 已
  // 转数据路由);同屏仅变查询参数(深链预选)不拦,预选自身有切文件
  // confirm 兜底。确认离开 = proceed,取消 = reset 留守(design §3 路由半边)
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    dirty && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    if (blocker.state !== "blocked") return;
    if (window.confirm("当前文件有未保存的修改,离开将丢失(可先保存或复制留底)。确定离开?")) {
      blocker.proceed();
    } else {
      blocker.reset();
    }
  }, [blocker]);

  const selectedFile = doc.status === "ready" || doc.status === "loading" ? doc.file : null;
  const draftName = docReady?.draft ? docReady.fileName : null;

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 pb-4">
      <PageHeader
        title="配置编辑"
        description="品类 YAML 原文编辑:注释逐字节保真;保存经同门校验(坏内容零写入),成功后 doctor 复核"
        actions={
          <>
            <Button
              size="sm"
              variant="outline"
              disabled={docReady === null || dirty || run.starting}
              title={dirty ? "先保存再运行" : "以当前品类发起一次采集(run.start)"}
              onClick={() => void handleRunOnce()}
            >
              <Play className="size-3.5" />
              跑一次
            </Button>
            <Button size="sm" variant="outline" disabled={list.status === "loading"} onClick={() => void reloadList()}>
              <RefreshCw className={list.status === "loading" ? "size-3.5 animate-spin" : "size-3.5"} />
              刷新
            </Button>
          </>
        }
      />

      {list.status === "error" && list.error ? (
        <div className="px-6">
          <ErrorBox error={list.error} onRetry={() => void reloadList()} />
        </div>
      ) : null}
      {actionError ? (
        <div className="px-6">
          <ErrorBox error={actionError} />
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 gap-4 px-6">
        <Card className="flex w-72 shrink-0 flex-col">
          <CardContent className="flex min-h-0 flex-1 flex-col p-3">
            {list.status === "loading" ? (
              <div className="flex flex-col gap-2" aria-label="文件列表加载中">
                {[0, 1, 2, 3].map((index) => (
                  <Skeleton key={index} className="h-10 w-full" />
                ))}
              </div>
            ) : list.status === "ready" ? (
              <FileList
                files={list.files}
                selectedFile={selectedFile}
                draftName={draftName}
                onSelect={(file) => void openFile(file)}
                onDelete={(entry) => void handleDelete(entry)}
                onCreate={(stem) => void createDraft(stem, list.pluginsDir)}
              />
            ) : null}
          </CardContent>
        </Card>

        <Card className="flex min-w-0 flex-1 flex-col">
          <CardContent className="flex min-h-0 flex-1 flex-col gap-2 p-3">
            {/* 标题区:文件名 + dirty 标记 + 完整路径(dev 模式即仓库路径,决议 10 保透明) */}
            <div className="flex items-center justify-between gap-2" data-testid="editor-title">
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="truncate text-sm font-medium text-foreground">
                  {docReady ? docReady.fileName : "未选择文件"}
                  {dirty ? " *" : ""}
                </span>
                {docReady?.draft ? <Badge variant="default">未保存草稿</Badge> : null}
              </span>
              <span
                className="max-w-[55%] truncate font-mono text-2xs text-muted-foreground"
                title={docReady ? docReady.file : undefined}
                data-testid="editor-path"
              >
                {docReady ? docReady.file : ""}
              </span>
            </div>

            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={docReady === null || validate.running}
                onClick={() => void runValidate()}
              >
                <ShieldCheck className="size-3.5" />
                {validate.running ? "校验中…" : "校验"}
              </Button>
              <Button
                size="sm"
                disabled={docReady === null || !dirty || save.saving}
                onClick={() => void saveFile()}
              >
                <Save className="size-3.5" />
                {save.saving ? "保存中…" : "保存"}
              </Button>
              <span className="text-2xs text-muted-foreground">⌘S 保存</span>
              {runNotice ? (
                <span
                  role="status"
                  data-testid="run-notice"
                  className="truncate text-2xs text-warning"
                >
                  {runNotice}
                </span>
              ) : null}
            </div>

            <div className="min-h-0 flex-1">
              {doc.status === "idle" ? (
                <EmptyState
                  compact
                  title="从左侧选择品类文件"
                  description="坏文件(损坏徽标)也能打开修复;「新建」从最小模板起草"
                />
              ) : doc.status === "loading" ? (
                <div className="flex h-full flex-col gap-2" aria-label="文件读取中">
                  <Skeleton className="h-full w-full" />
                </div>
              ) : doc.status === "error" ? (
                <ErrorBox error={doc.error} onRetry={() => void openFile(doc.file)} />
              ) : (
                <EditorPane value={doc.content} onChange={setContent} className="h-full" />
              )}
            </div>

            {/* 结果区:校验 findings / 保存结构化错误 / doctor 复核 / 跑一次去向 */}
            <div className="flex max-h-44 shrink-0 flex-col gap-1.5 overflow-y-auto border-t border-border pt-2">
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

              {save.error ? <ErrorBox error={save.error} /> : null}
              {save.created !== null && save.error === null ? (
                <p role="status" data-testid="save-ok" className="text-xs text-ok">
                  已保存{save.created ? "(新建)" : ""} · mtime 基线已更新
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

              {run.runId !== null ? (
                <p role="status" data-testid="run-started" className="text-xs text-foreground">
                  已发起采集(run #{run.runId});
                  <Link to="/logs" className="ml-0.5 text-primary underline-offset-2 hover:underline">
                    去日志屏查看
                  </Link>
                </p>
              ) : null}
              {run.error ? <ErrorBox error={run.error} /> : null}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
