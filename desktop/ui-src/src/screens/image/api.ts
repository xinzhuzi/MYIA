/**
 * 看图数据装配(本屏私有 api 模块;共享客户端 @/lib/api 只读不动)。
 *
 * 数据面 = sidecar 协议 image.* 方法族(契约权威:任务 10-03-image-input design.md
 * 协议表;entry.py `_HANDLERS` 双侧同步 —— Python 侧收编前运行时得结构化
 * method_not_found,界面按错误态如实呈现,与 sources.write 同一惯例)。
 *
 *   image.import {kind: path|base64, value, mime?} → {id, path, bytes, ext}
 *   image.ocr   {id, engine?}                     → {lines:[{text,conf}], engine, ms}
 *   image.analyze {id, mode, question?, channel?} → {job_id}(结果走事件流)
 *   image.status {}                               → {busy, job_id?}
 *   image.config.read {}                          → 脱敏配置(VisionConfig)
 *   image.config.save {config}                    → {ok}
 *
 * 事件:image.progress / image.completed(壳转发 sidecar://event;订阅走共享
 * onSidecarEvent)。
 *
 * 图片进入 webview 的四条路(前三条零权限):
 *   拖拽(Tauri webview 拖放事件,给路径)/ 拖拽(HTML5 drop,给 File)/
 *   粘贴(clipboard File)/ 系统选择器(tauri-plugin-dialog,给路径)。
 * 路径 → image.import kind=path;File → base64 → kind=base64 + 缩略图 data: URL
 * (CSP img-src data: 已放行;blob: 不在名单,故必须用 FileReader.readAsDataURL)。
 */
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

import { SidecarRequestError } from "@/lib/api";
import type {
  AnalyzeMode,
  ImageAnalyzeResult,
  ImageImportResult,
  ImageOcrResult,
  ImageStatusResult,
  OcrEngine,
  SidecarErrorShape,
  VisionChannel,
  VisionConfig,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// 错误归一化(与 sources/settings 屏同规则;独立实现避免动共享层)
// ---------------------------------------------------------------------------

/** 任意抛出物 → SidecarRequestError(组件渲染 code/path/message 用)。 */
export function asSidecarError(raw: unknown): SidecarRequestError {
  if (raw instanceof SidecarRequestError) return raw;
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw) as Partial<SidecarErrorShape>;
      if (parsed && typeof parsed.code === "string" && typeof parsed.message === "string") {
        return new SidecarRequestError({
          code: parsed.code,
          path: typeof parsed.path === "string" ? parsed.path : "$",
          message: parsed.message,
          data: parsed.data,
        });
      }
    } catch {
      // 非 JSON 文本:按裸消息包装
    }
    return new SidecarRequestError({ code: "transport_error", path: "$", message: raw });
  }
  return new SidecarRequestError({
    code: "sidecar_unavailable",
    path: "$",
    message: raw instanceof Error ? `Tauri IPC 不可用: ${raw.message}` : `Tauri IPC 不可用: ${String(raw)}`,
  });
}

// ---------------------------------------------------------------------------
// 协议方法封装(走壳命令 sidecar_request;方法名与 entry.py 双侧同步)
// ---------------------------------------------------------------------------

/** 系统选择器/拖放路径 → 落库 MYIA_HOME/images(heic 经 sips 转 png 后收)。 */
export async function importImageByPath(path: string): Promise<ImageImportResult> {
  try {
    return await invoke<ImageImportResult>("sidecar_request", {
      method: "image.import",
      params: { kind: "path", value: path },
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 拖/贴的 File(已读 base64)→ 落库;mime 供扩展名与 sips 判型。 */
export async function importImageByBase64(base64: string, mime: string): Promise<ImageImportResult> {
  try {
    return await invoke<ImageImportResult>("sidecar_request", {
      method: "image.import",
      params: { kind: "base64", value: base64, mime },
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 一级 OCR(双引擎);engine 缺省由 sidecar 取配置默认。 */
export async function runImageOcr(id: string, engine?: OcrEngine): Promise<ImageOcrResult> {
  try {
    return await invoke<ImageOcrResult>("sidecar_request", {
      method: "image.ocr",
      params: { id, ...(engine ? { engine } : {}) },
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 二级看图:返回 job_id 即返;结果走 image.progress/image.completed 事件流。 */
export async function startImageAnalyze(params: {
  id: string;
  mode: AnalyzeMode;
  question?: string;
  channel?: VisionChannel;
}): Promise<ImageAnalyzeResult> {
  try {
    return await invoke<ImageAnalyzeResult>("sidecar_request", {
      method: "image.analyze",
      params,
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 看图任务对账(重连/进屏时是否已有 job 在跑)。 */
export async function imageStatus(): Promise<ImageStatusResult> {
  try {
    return await invoke<ImageStatusResult>("sidecar_request", {
      method: "image.status",
      params: {},
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 读看图结构配置(vision.yaml;keychain 引用不回明文)。 */
export async function readImageConfig(): Promise<VisionConfig> {
  try {
    return await invoke<VisionConfig>("sidecar_request", {
      method: "image.config.read",
      params: {},
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 写看图结构配置(同门校验失败零写入);设置屏 VisionForm 复用。 */
export async function saveImageConfig(config: VisionConfig): Promise<{ ok: true }> {
  try {
    return await invoke<{ ok: true }>("sidecar_request", {
      method: "image.config.save",
      params: { config },
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

// ---------------------------------------------------------------------------
// 文件进入与校验(纯 JS,零权限)
// ---------------------------------------------------------------------------

/** 收图格式白名单(与 sidecar image.import 同口径;heic 由 sidecar sips 转 png) */
export const IMAGE_ACCEPT_EXTS = ["png", "jpg", "jpeg", "webp", "heic"] as const;

/** 大小上限 10MB(与 sidecar image_too_large 同口径;前端先挡一道) */
export const IMAGE_SIZE_LIMIT_BYTES = 10 * 1024 * 1024;

/** 文件名 → 小写扩展名(无点);「a.b.PNG」→「png」 */
export function extOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot + 1).toLowerCase();
}

/** 前端预检:格式/大小;通过返回 null,否则返回中文原因。 */
export function validateImageFile(file: { name: string; size: number }): string | null {
  const ext = extOf(file.name);
  if (!ext || !(IMAGE_ACCEPT_EXTS as readonly string[]).includes(ext)) {
    return `不支持的格式 .${ext || "(无扩展名)"}:只收 ${IMAGE_ACCEPT_EXTS.join(" / ")}`;
  }
  if (file.size > IMAGE_SIZE_LIMIT_BYTES) {
    return `图片超过 10MB 上限(${(file.size / 1024 / 1024).toFixed(1)}MB)`;
  }
  return null;
}

/** File → data URL + 裸 base64(FileReader;jsdom/Tauri webview 均原生支持)。 */
export function readFileAsDataUrl(file: File): Promise<{ dataUrl: string; base64: string; mime: string }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(asSidecarError(reader.error ?? "读取文件失败"));
    reader.onload = () => {
      const dataUrl = String(reader.result);
      const comma = dataUrl.indexOf(",");
      resolve({
        dataUrl,
        base64: comma === -1 ? "" : dataUrl.slice(comma + 1),
        mime: file.type || "application/octet-stream",
      });
    };
    reader.readAsDataURL(file);
  });
}

/** 系统选择器(tauri-plugin-dialog);取消返回 null。 */
export async function selectImageViaDialog(): Promise<string | null> {
  const selection = await open({
    multiple: false,
    title: "选择图片",
    filters: [{ name: "图片", extensions: [...IMAGE_ACCEPT_EXTS] }],
  });
  return typeof selection === "string" ? selection : null;
}

// ---------------------------------------------------------------------------
// 置信度色阶与出网知情(纯函数 + 本地存储)
// ---------------------------------------------------------------------------

/** 置信度三档色阶;low = ≤0.5(标警示 + 手动升二级;阈值两引擎统一,刻度不互比) */
export type ConfTone = "low" | "mid" | "high";

export function confTone(conf: number): ConfTone {
  if (conf <= 0.5) return "low";
  if (conf <= 0.9) return "mid";
  return "high";
}

/** 云端出网知情确认的本地记忆键(v1 不入库:一次知情,本机记住) */
export const CLOUD_CONSENT_STORAGE_KEY = "myia.image.cloudConsent.v1";

export function hasCloudConsent(): boolean {
  try {
    return window.localStorage.getItem(CLOUD_CONSENT_STORAGE_KEY) === "acknowledged";
  } catch {
    return false; // localStorage 不可用:每次都重新确认,宁烦不默许
  }
}

export function rememberCloudConsent(): void {
  try {
    window.localStorage.setItem(CLOUD_CONSENT_STORAGE_KEY, "acknowledged");
  } catch {
    // 记不住就算了:下次切换再确认一次
  }
}

/** 结果文本复制:navigator.clipboard 优先,失败返回 false(UI 退化手动选中复制)。 */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// 缺省配置(sidecar 未实现/未配置时的回退;形状与 vision.yaml 缺省一致)
// ---------------------------------------------------------------------------

export const DEFAULT_VISION_CONFIG: VisionConfig = {
  channel_default: "local",
  local: { base_url: "http://127.0.0.1:8080", model: "" },
  cloud: { base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4.6v", api_key: null },
  ocr: { enabled: true, engine_default: "vision" },
};

/** 路径最后一段(展示名;「/a/b/shot.png」→「shot.png」)。 */
export function basename(path: string): string {
  const slash = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return slash === -1 ? path : path.slice(slash + 1);
}
