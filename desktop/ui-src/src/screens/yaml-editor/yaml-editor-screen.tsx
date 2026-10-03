import { Play, RefreshCw, Save, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { SidecarRequestError } from "@/lib/api";

import { EditorPane } from "./editor-pane";
import { FileList } from "./file-list";
import { FindingsPanel } from "./findings-panel";
import { asSidecarError, deleteYaml, getYamlTemplate, listYamlFiles, readYaml, saveYaml, validateYaml } from "./api";
import type { YamlFileEntry, YamlFinding, YamlValidateResult } from "./api";

/** 列表态(loading/error/ready;坏文件也入列,交给 FileList 打损坏徽标) */
interface ListState {
  status: "loading" | "ready" | "error";
  files: YamlFileEntry[];
  pluginsDir: string;
  error: SidecarRequestError | null;
}

/** 编辑器文档态:idle(未选)→ loading → ready/error;ready 含未保存草稿(draft) */
type DocState =
  | { status: "idle" }
  | { status: "loading"; file: string }
  | { status: "error"; file: string; error: SidecarRequestError }
  | {
      status: "ready";
      file: string;
      fileName: string;
      /** 编辑器当前内容 */
      content: string;
      /** 上次读盘/保存的原文;content !== savedContent 即 dirty */
      savedContent: string;
      /** save 乐观锁基线;新建草稿 = null(save 走新建语义) */
      mtime: number | null;
      /** 未保存草稿(模板回填而来;保存成功转正常编辑态) */
      draft: boolean;
    };

/** 干跑校验视图(result 在内容变更时清空,防陈旧 findings 误导) */
interface ValidateView {
  running: boolean;
  result: YamlValidateResult | null;
  error: SidecarRequestError | null;
}

/** 保存成功后的 doctor 复核(决议 7:「myia 真认」的证据;失败也如实报) */
interface DoctorCheck {
  ok: boolean;
  message: string;
}

interface SaveView {
  saving: boolean;
  created: boolean | null;
  warnings: YamlFinding[];
  doctor: DoctorCheck | null;
  error: SidecarRequestError | null;
}

interface RunView {
  starting: boolean;
  runId: number | null;
  error: SidecarRequestError | null;
}

const INITIAL_VALIDATE: ValidateView = { running: false, result: null, error: null };
const INITIAL_SAVE: SaveView = { saving: false, created: null, warnings: [], doctor: null, error: null };
const INITIAL_RUN: RunView = { starting: false, runId: null, error: null };

/** 路径比较用归一(Windows 分隔符统一;不涉及语义解析,围栏在后端) */
function sameFilePath(a: string, b: string): boolean {
  return a.replace(/\\/g, "/") === b.replace(/\\/g, "/");
}

function fileNameOf(file: string): string {
  const parts = file.split(/[\\/]/);
  return parts[parts.length - 1] ?? file;
}

function joinPath(dir: string, name: string): string {
  return dir.endsWith("/") || dir.endsWith("\\") ? `${dir}${name}` : `${dir}/${name}`;
}

/**
 * 配置编辑第六屏:左文件列表 + 右 CodeMirror 原文编辑。
 *
 * 写链路:yaml.save(同门校验 error 级零容忍零写入 → .bak → 原子落盘,mtime
 * 乐观锁)→ 成功自动 doctor({yamls:[file]}) 复核 + 「跑一次」(run.start,
 * dirty 禁用);失败结构化错误展示,绝不假装成功。dirty 守卫 = 切文件/新建/
 * 删除前 window.confirm + beforeunload(应用级 v1;SPA 内路由拦截需数据路由
 * 改造,超本步范围)。源管理行「编辑」经 /yaml-editor?file=… 预选。
 */
export function YamlEditorScreen() {
  const [list, setList] = useState<ListState>({ status: "loading", files: [], pluginsDir: "", error: null });
  const [doc, setDoc] = useState<DocState>({ status: "idle" });
  const [validate, setValidate] = useState<ValidateView>(INITIAL_VALIDATE);
  const [save, setSave] = useState<SaveView>(INITIAL_SAVE);
  const [run, setRun] = useState<RunView>(INITIAL_RUN);
  /** 删除等列表动作的结构化错误(列表整体仍可用,故不进 ListState.error) */
  const [actionError, setActionError] = useState<SidecarRequestError | null>(null);
  /** 采集运行中提示(只提示不拦:run 启动时已读完 YAML,中途改文件无害) */
  const [runNotice, setRunNotice] = useState<string | null>(null);
  /** 快速切换文件时丢弃过期应答(openFile/草稿共用一个序号泉) */
  const docSeq = useRef(0);

  const docReady = doc.status === "ready" ? doc : null;
  const dirty = docReady !== null && docReady.content !== docReady.savedContent;

  const resetPanes = useCallback(() => {
    setValidate(INITIAL_VALIDATE);
    setSave(INITIAL_SAVE);
    setRun(INITIAL_RUN);
    setRunNotice(null);
    setActionError(null);
  }, []);

  const reloadList = useCallback(async () => {
    setList({ status: "loading", files: [], pluginsDir: "", error: null });
    try {
      const result = await listYamlFiles();
      setList({ status: "ready", files: result.files, pluginsDir: result.plugins_dir, error: null });
    } catch (error) {
      setList({ status: "error", files: [], pluginsDir: "", error: asSidecarError(error) });
    }
  }, []);

  useEffect(() => {
    void reloadList();
  }, [reloadList]);

  /** dirty 守卫:放弃编辑前 window.confirm(v1 够用不花哨,design §3) */
  const confirmDiscardIfDirty = useCallback((): boolean => {
    if (!dirty) return true;
    return window.confirm("当前文件有未保存的修改,放弃后将丢失(可先保存或复制留底)。确定继续?");
  }, [dirty]);

  const openFile = useCallback(
    async (file: string) => {
      if (!confirmDiscardIfDirty()) return;
      const seq = ++docSeq.current;
      resetPanes();
      setDoc({ status: "loading", file });
      try {
        const result = await readYaml(file);
        if (seq !== docSeq.current) return;
        setDoc({
          status: "ready",
          file: result.file,
          fileName: fileNameOf(result.file),
          content: result.content,
          savedContent: result.content,
          mtime: result.mtime,
          draft: false,
        });
      } catch (error) {
        if (seq !== docSeq.current) return;
        setDoc({ status: "error", file, error: asSidecarError(error) });
      }
    },
    [confirmDiscardIfDirty, resetPanes],
  );

  /** 源管理「编辑」跳转预选:?file=… 只应用一次,后续由用户选择主导 */
  const [searchParams] = useSearchParams();
  const preselectFile = searchParams.get("file");
  const appliedPreselect = useRef<string | null>(null);
  useEffect(() => {
    if (preselectFile === null || appliedPreselect.current === preselectFile) return;
    appliedPreselect.current = preselectFile;
    void openFile(preselectFile);
  }, [preselectFile, openFile]);

  /** 新建流:stem 已过 FileList 前端正则预检 → 模板 → 回填 id → 未保存草稿态 */
  const createDraft = useCallback(
    async (stem: string) => {
      if (!confirmDiscardIfDirty()) return;
      const seq = ++docSeq.current;
      resetPanes();
      try {
        const template = await getYamlTemplate();
        if (seq !== docSeq.current) return;
        const content = template.content.replace("id: my-category", `id: ${stem}`);
        const file = joinPath(list.pluginsDir || "plugins", `${stem}.yaml`);
        setDoc({
          status: "ready",
          file,
          fileName: `${stem}.yaml`,
          content,
          savedContent: "",
          mtime: null,
          draft: true,
        });
      } catch (error) {
        if (seq !== docSeq.current) return;
        setDoc({ status: "error", file: `${stem}.yaml`, error: asSidecarError(error) });
      }
    },
    [confirmDiscardIfDirty, list.pluginsDir, resetPanes],
  );

  const handleContentChange = useCallback((next: string) => {
    setDoc((prev) => (prev.status === "ready" ? { ...prev, content: next } : prev));
    // 内容变了,旧 findings/保存结果/复核即过期:清空防误导
    setValidate((prev) => (prev.result !== null || prev.error !== null ? INITIAL_VALIDATE : prev));
    setSave((prev) =>
      prev.created !== null || prev.doctor !== null || prev.error !== null || prev.warnings.length > 0
        ? { ...INITIAL_SAVE, saving: prev.saving }
        : prev,
    );
  }, []);

  /** 采集运行中检查(只提示不拦):发起保存/跑一次前查 run.status */
  const checkRunInFlight = useCallback(async (file: string) => {
    try {
      const status = await api.runStatus();
      const inflight = status.runs.find((entry) => entry.state === "running" && sameFilePath(entry.yaml, file));
      if (inflight) {
        setRunNotice(`采集进行中(run #${inflight.run_id}),改动下一次运行生效`);
      }
    } catch {
      // run.status 不可用不拦主流程:提示是锦上添花
    }
  }, []);

  /** 校验干跑:零写入,findings 分级展示 */
  const handleValidate = useCallback(async () => {
    if (docReady === null || validate.running) return;
    setValidate({ running: true, result: null, error: null });
    try {
      const result = await validateYaml(docReady.content, docReady.file);
      setValidate({ running: false, result, error: null });
    } catch (error) {
      setValidate({ running: false, result: null, error: asSidecarError(error) });
    }
  }, [docReady, validate.running]);

  /** 保存流:失败结构化错误(绝不假装成功);成功 → 更新基线 → doctor 复核 → 刷新列表 */
  const handleSave = useCallback(async () => {
    if (docReady === null || save.saving) return;
    if (!docReady.draft && !dirty) return;
    const { file, content, mtime } = docReady;
    setSave({ ...INITIAL_SAVE, saving: true });
    setRunNotice(null);
    void checkRunInFlight(file);
    try {
      const result = await saveYaml(file, content, mtime);
      // 落盘成功:基线前移(mtime = 下次乐观锁对照值);草稿转正常编辑态
      setDoc((prev) =>
        prev.status === "ready" && sameFilePath(prev.file, result.file)
          ? { ...prev, file: result.file, savedContent: content, mtime: result.mtime, draft: false }
          : prev,
      );
      let doctor: DoctorCheck;
      try {
        const check = await api.doctor({ yamls: [result.file] });
        const plugin = check.plugins.find((candidate) => sameFilePath(candidate.file, result.file));
        doctor = plugin
          ? {
              ok: true,
              message: `doctor 复核通过:识别「${plugin.name ?? plugin.id ?? fileNameOf(result.file)}」(${plugin.sources.length} 源)`,
            }
          : { ok: false, message: `doctor 未报告该品类(${result.file});请到仪表屏手动诊断` };
      } catch (error) {
        doctor = { ok: false, message: `doctor 复核失败:${asSidecarError(error).message}` };
      }
      setSave({ saving: false, created: result.created, warnings: result.warnings, doctor, error: null });
      void reloadList();
    } catch (error) {
      setSave({ ...INITIAL_SAVE, error: asSidecarError(error) });
    }
  }, [checkRunInFlight, dirty, docReady, reloadList, save.saving]);

  /** 跑一次:复用 run.start;dirty 禁用(title 提示先保存);发起后引导去日志屏 */
  const handleRunOnce = useCallback(async () => {
    if (docReady === null || dirty || run.starting) return;
    setRun((prev) => ({ ...INITIAL_RUN, starting: true, error: prev.error }));
    setRunNotice(null);
    void checkRunInFlight(docReady.file);
    try {
      const started = await api.runStart({ yaml: docReady.file });
      setRun({ starting: false, runId: started.run_id, error: null });
    } catch (error) {
      setRun({ starting: false, runId: null, error: asSidecarError(error) });
    }
  }, [checkRunInFlight, dirty, docReady, run.starting]);

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
        docSeq.current += 1; // 使在途读取应答作废
        if (
          (doc.status === "ready" || doc.status === "loading") &&
          sameFilePath(doc.file, entry.file)
        ) {
          setDoc({ status: "idle" });
          resetPanes();
        }
        await reloadList();
      } catch (error) {
        setActionError(asSidecarError(error));
      }
    },
    [doc, reloadList, resetPanes],
  );

  // Cmd+S = 保存(preventDefault 防 webview 默认行为;编辑器无快捷键等于没腿)
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void handleSave();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [handleSave]);

  // 应用级 dirty 守卫:窗口关闭/刷新(SPA 内路由拦截需数据路由改造,超出 v1)
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

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
          <ListErrorBox error={list.error} onRetry={() => void reloadList()} />
        </div>
      ) : null}
      {actionError ? (
        <div className="px-6">
          <ListErrorBox error={actionError} />
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
                onCreate={(stem) => void createDraft(stem)}
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
                className="max-w-[55%] truncate font-mono text-[11px] text-muted-foreground"
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
                onClick={() => void handleValidate()}
              >
                <ShieldCheck className="size-3.5" />
                {validate.running ? "校验中…" : "校验"}
              </Button>
              <Button
                size="sm"
                disabled={docReady === null || !dirty || save.saving}
                onClick={() => void handleSave()}
              >
                <Save className="size-3.5" />
                {save.saving ? "保存中…" : "保存"}
              </Button>
              <span className="text-[11px] text-muted-foreground">⌘S 保存</span>
              {runNotice ? (
                <span
                  role="status"
                  data-testid="run-notice"
                  className="truncate text-[11px] text-warning"
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
                <ListErrorBox error={doc.error} onRetry={() => void openFile(doc.file)} />
              ) : (
                <EditorPane value={doc.content} onChange={handleContentChange} className="h-full" />
              )}
            </div>

            {/* 结果区:校验 findings / 保存结构化错误 / doctor 复核 / 跑一次去向 */}
            <div className="flex max-h-44 shrink-0 flex-col gap-1.5 overflow-y-auto border-t border-border pt-2">
              {validate.error ? <ListErrorBox error={validate.error} /> : null}
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

              {save.error ? <ListErrorBox error={save.error} /> : null}
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
              {run.error ? <ListErrorBox error={run.error} /> : null}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

/** 本屏私有错误框:与 sources 屏同款结构化呈现(code/path/message 全量如实) */
function ListErrorBox({ error, onRetry }: { error: SidecarRequestError; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs">
      <p className="font-medium text-destructive">{error.message}</p>
      <p className="font-mono text-[11px] text-muted-foreground">
        code={error.code} path={error.path}
      </p>
      {onRetry ? (
        <Button variant="outline" size="sm" className="mt-1" onClick={onRetry}>
          重试
        </Button>
      ) : null}
    </div>
  );
}
