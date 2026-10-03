import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { SidecarRequestError } from "@/lib/api";

import { asSidecarError, getYamlTemplate, readYaml, saveYaml, validateYaml } from "./api";
import type { YamlFinding, YamlValidateResult } from "./api";

/**
 * 单文件编辑内核(共享 hook):双栏配置编辑屏(yaml-editor-screen.tsx)与
 * 源管理弹窗(yaml-editor-dialog.tsx)共用,**不许出现第二份保存逻辑**。
 *
 * 状态机:读取 yaml.read(原文逐字节)→ dirty 跟踪(content !== savedContent)
 * → 校验 yaml.validate 干跑(findings 分级)→ 保存 yaml.save(mtime 乐观锁;
 * 失败结构化错误态,绝不假装成功)→ 成功后 doctor({yamls:[file]}) 复核展示。
 * 列表/新建入口/删除/跑一次属调用方(屏)职责,不在此 hook。
 */

/** 编辑器文档态:idle(未选)→ loading → ready/error;ready 含未保存草稿(draft) */
export type DocState =
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
export interface DoctorCheck {
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

const INITIAL_VALIDATE: ValidateView = { running: false, result: null, error: null };
const INITIAL_SAVE: SaveView = { saving: false, created: null, warnings: [], doctor: null, error: null };

/** 路径比较用归一(Windows 分隔符统一;不涉及语义解析,围栏在后端) */
export function sameFilePath(a: string, b: string): boolean {
  return a.replace(/\\/g, "/") === b.replace(/\\/g, "/");
}

export function fileNameOf(file: string): string {
  const parts = file.split(/[\\/]/);
  return parts[parts.length - 1] ?? file;
}

function joinPath(dir: string, name: string): string {
  return dir.endsWith("/") || dir.endsWith("\\") ? `${dir}${name}` : `${dir}/${name}`;
}

export interface UseYamlFileEditorOptions {
  /** 保存成功后回调(双栏屏:刷新左栏列表;弹窗可不传) */
  onSaved?: () => void;
  /** 打开/新建文件时(dirty 守卫通过后)的伴随重置(双栏屏:清跑一次结果与列表动作错误) */
  onOpenStart?: () => void;
}

/**
 * 用法:const editor = useYamlFileEditor({ onSaved });
 * 返回的各回调为稳定引用(依赖 dirty 的守卫除外),可直接进 effect 依赖。
 */
export function useYamlFileEditor(options: UseYamlFileEditorOptions = {}) {
  const { onSaved, onOpenStart } = options;
  const [doc, setDoc] = useState<DocState>({ status: "idle" });
  const [validate, setValidate] = useState<ValidateView>(INITIAL_VALIDATE);
  const [save, setSave] = useState<SaveView>(INITIAL_SAVE);
  /** 采集运行中提示(只提示不拦:run 启动时已读完 YAML,中途改文件无害) */
  const [runNotice, setRunNotice] = useState<string | null>(null);
  /** 快速切换文件时丢弃过期应答(openFile/草稿共用一个序号泉) */
  const docSeq = useRef(0);

  const docReady = doc.status === "ready" ? doc : null;
  const dirty = docReady !== null && docReady.content !== docReady.savedContent;

  /** 清校验/保存/运行提示(不动 doc;打开新文件与关闭前重置用) */
  const resetResults = useCallback(() => {
    setValidate(INITIAL_VALIDATE);
    setSave(INITIAL_SAVE);
    setRunNotice(null);
  }, []);

  /** dirty 守卫:放弃编辑前 window.confirm(v1 够用不花哨,design §3;弹窗关闭同款) */
  const confirmDiscardIfDirty = useCallback((): boolean => {
    if (!dirty) return true;
    return window.confirm("当前文件有未保存的修改,放弃后将丢失(可先保存或复制留底)。确定继续?");
  }, [dirty]);

  const openFile = useCallback(
    async (file: string) => {
      if (!confirmDiscardIfDirty()) return;
      const seq = ++docSeq.current;
      onOpenStart?.();
      resetResults();
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
    [confirmDiscardIfDirty, onOpenStart, resetResults],
  );

  /** 新建流后半段:stem 已过前端正则预检 → 取模板 → 回填 id → 未保存草稿态 */
  const createDraft = useCallback(
    async (stem: string, pluginsDir: string) => {
      if (!confirmDiscardIfDirty()) return;
      const seq = ++docSeq.current;
      onOpenStart?.();
      resetResults();
      try {
        const template = await getYamlTemplate();
        if (seq !== docSeq.current) return;
        const content = template.content.replace("id: my-category", `id: ${stem}`);
        const file = joinPath(pluginsDir || "plugins", `${stem}.yaml`);
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
    [confirmDiscardIfDirty, onOpenStart, resetResults],
  );

  const setContent = useCallback((next: string) => {
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
  const runValidate = useCallback(async () => {
    if (docReady === null || validate.running) return;
    setValidate({ running: true, result: null, error: null });
    try {
      const result = await validateYaml(docReady.content, docReady.file);
      setValidate({ running: false, result, error: null });
    } catch (error) {
      setValidate({ running: false, result: null, error: asSidecarError(error) });
    }
  }, [docReady, validate.running]);

  /** 保存流:失败结构化错误(绝不假装成功);成功 → 更新基线 → doctor 复核 → onSaved */
  const saveFile = useCallback(async () => {
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
      onSaved?.();
    } catch (error) {
      setSave({ ...INITIAL_SAVE, error: asSidecarError(error) });
    }
  }, [checkRunInFlight, dirty, docReady, onSaved, save.saving]);

  /** 清采集运行提示(跑一次前:旧提示随新动作过期) */
  const clearRunNotice = useCallback(() => setRunNotice(null), []);

  // 应用级 dirty 守卫:窗口关闭/刷新(屏与弹窗共用同一份;SPA 路由级守卫仍归调用方)
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  /** 作废在途应答(删除非选中文件等场景:更早发起的读取回来越过最新状态) */
  const invalidate = useCallback(() => {
    docSeq.current += 1;
  }, []);

  /** 回 idle:作废在途应答 + 清 doc 与全部结果(选中文件被删/弹窗关闭重置) */
  const clear = useCallback(() => {
    docSeq.current += 1;
    setDoc({ status: "idle" });
    resetResults();
  }, [resetResults]);

  return {
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
    resetResults,
    clearRunNotice,
    confirmDiscardIfDirty,
  };
}
