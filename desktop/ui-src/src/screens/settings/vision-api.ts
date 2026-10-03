/**
 * 看图分区协议封装(10-03-vision-pipeline 拆屏:看图屏整拆后 image.* 配置面
 * = image.config.read / image.config.save 由设置屏 VisionForm 使用;
 * 10-03-vision-v2 增模型管理六方法 image.models.* / image.server.*,
 * 仍走本模块屏私有封装 —— image.* 家族不进共享门面)。
 *
 * 数据面 = sidecar 协议:
 *   image.config.read {}       → {file, exists, config}(config 为脱敏
 *                                VisionConfig;本模块解包返 config)
 *   image.config.save {config} → {ok}(同门校验失败零写入)
 *   image.models.list {}       → {models:[{name,path,bytes,active,incomplete}]}
 *   image.models.download {repo, name?} → {job_id}(异步,事件见下)
 *   image.models.delete {name} / image.models.activate {name} → {ok}
 *   image.server.status {}     → {running, base_url, model, healthy}
 *   image.server.ensure {}     → 已健康 = status+{started};否则快照超集+
 *                                {ensuring:true, job_id},终态走事件(见下)
 *   image.files.purge {days}   → {deleted, bytes_freed}(CLI 面能力,零 UI)
 *   事件 image.models.progress {job_id, repo, done_bytes, total_bytes?, ts} /
 *        image.models.completed {job_id, ok, error?, ts} /
 *        image.server.completed {job_id, ok, status?, error?, ts}
 *        (均经 onSidecarEvent)
 *
 * 惯例与 sources/settings 屏一致:invoke 直连壳命令 `sidecar_request` +
 * asSidecarError 归一化(错误必得 code/path/message)。
 */
import { invoke } from "@tauri-apps/api/core";

import type {
  ImageConfigReadResult,
  ImageModelsActivateParams,
  ImageModelsDeleteParams,
  ImageModelsDownloadParams,
  ImageModelsDownloadResult,
  ImageModelsListResult,
  ImageModelsMutationResult,
  ImageServerEnsureResult,
  ImageServerStatusResult,
  VisionConfig,
  VisionModelEntry,
} from "@/lib/api";

import { asSidecarError } from "./api";

// ---------------------------------------------------------------------------
// 协议方法封装(走壳命令 sidecar_request;方法名与 entry.py 双侧同步)
// ---------------------------------------------------------------------------

/** 读看图结构配置(vision.yaml;keychain 引用不回明文)。
 *  协议应答是 {file, exists, config} 包装(entry.py 锁定),此处解包返 config;
 *  mock/异常应答缺 config 字段时原样透传,由调用方形状防御如实呈现。 */
export async function readImageConfig(): Promise<VisionConfig> {
  try {
    const response = await invoke<ImageConfigReadResult>("sidecar_request", {
      method: "image.config.read",
      params: {},
    });
    if (response && typeof response === "object" && "config" in response) {
      return response.config;
    }
    return response as unknown as VisionConfig;
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
// image.models.* / image.server.* 六方法(10-03-vision-v2;沿用本模块 image.*
// 屏私有封装惯例,invoke 直连 + asSidecarError 归一化):
//   image.models.list       {}                 → {models:[{name,path,bytes,
//                                                active,incomplete}]}
//   image.models.download   {repo, name?}      → {job_id}(异步;进度/终态走
//                                                image.models.progress/completed;
//                                                完整同名 model_exists 拒)
//   image.models.delete     {name}             → {ok}(active 拒删 model_active_refused)
//   image.models.activate   {name}             → {ok}(vision.yaml local.model 改写;
//                                                半成品拒 model_incomplete)
//   image.server.status     {}                 → {running,base_url,model,healthy}
//   image.server.ensure     {}                 → 已健康=status+{started} 即返;
//                                                否则 +{ensuring,job_id} 应答即返,
//                                                终态走 image.server.completed 事件
// ---------------------------------------------------------------------------

/** 已装模型清单(models/ 一级子目录;空目录 = 合法空表)。 */
export async function listImageModels(): Promise<VisionModelEntry[]> {
  try {
    const result = await invoke<ImageModelsListResult>("sidecar_request", {
      method: "image.models.list",
      params: {},
    });
    // mock/异常应答缺 models 字段时防御为空表,由调用方如实呈现
    return Array.isArray(result?.models) ? result.models : [];
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 提交模型下载(异步 job;结果订阅 image.models.progress / completed 事件)。
 *  repo 必须 mlx-community/<name>(前端 IMAGE_MODELS_REPO_RE 同口径预校验)。 */
export async function downloadImageModel(
  params: ImageModelsDownloadParams,
): Promise<ImageModelsDownloadResult> {
  try {
    return await invoke<ImageModelsDownloadResult>("sidecar_request", {
      method: "image.models.download",
      params,
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 删除模型目录(active 拒删:在用权重删除会让本地 VL 突然失效)。 */
export async function deleteImageModel(
  params: ImageModelsDeleteParams,
): Promise<ImageModelsMutationResult> {
  try {
    return await invoke<ImageModelsMutationResult>("sidecar_request", {
      method: "image.models.delete",
      params,
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 激活模型 = vision.yaml local.model 指向该目录(同门校验原子写)。 */
export async function activateImageModel(
  params: ImageModelsActivateParams,
): Promise<ImageModelsMutationResult> {
  try {
    return await invoke<ImageModelsMutationResult>("sidecar_request", {
      method: "image.models.activate",
      params,
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 本地 mlx_vlm.server 状态(base_url/models 2s 探;零副作用)。 */
export async function imageServerStatus(): Promise<ImageServerStatusResult> {
  try {
    return await invoke<ImageServerStatusResult>("sidecar_request", {
      method: "image.server.status",
      params: {},
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 确保本地 mlx_vlm.server 在跑。快路径(已健康)应答即终态;慢路径应答
 *  立即返回 {ensuring:true, job_id}(自起 + 健康等待 ≤120s 跑 sidecar 后台
 *  线程,绝不冻结桌面协议),终态订阅 image.server.completed 事件;并发
 *  第二单抛 ensure_busy —— 等待态文案与翻徽章见 VisionModelsCard 服务行。 */
export async function ensureImageServer(): Promise<ImageServerEnsureResult> {
  try {
    return await invoke<ImageServerEnsureResult>("sidecar_request", {
      method: "image.server.ensure",
      params: {},
    });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

// ---------------------------------------------------------------------------
// 缺省配置(sidecar 未实现/未配置时的回退;形状与 vision.yaml 缺省一致)
// ---------------------------------------------------------------------------

export const DEFAULT_VISION_CONFIG: VisionConfig = {
  channel_default: "local",
  // 带 /v1 后缀,与 Python 缺省一致(settings.py DEFAULT_LOCAL_BASE_URL:SDK 按
  // {base_url}/chat/completions 发请求,mlx-vlm 端点在 /v1 下,不带后缀 404)
  local: { base_url: "http://127.0.0.1:8080/v1", model: "" },
  cloud: { base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4.6v", api_key: null },
  ocr: { enabled: true, engine_default: "vision" },
};
