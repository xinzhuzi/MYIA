import { Download, HardDrive, KeyRound, Save, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SidecarRequestError, onSidecarEvent } from "@/lib/api";
import type {
  ImageServerStatusResult,
  OcrEngine,
  UnlistenFn,
  VisionChannel,
  VisionConfig,
  VisionModelEntry,
} from "@/lib/api";
import { IMAGE_MODELS_NAME_RE, IMAGE_MODELS_REPO_RE } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  DEFAULT_VISION_CONFIG,
  activateImageModel,
  deleteImageModel,
  downloadImageModel,
  ensureImageServer,
  imageServerStatus,
  listImageModels,
  readImageConfig,
  saveImageConfig,
} from "./vision-api";

import { saveSecret } from "./api";
import { ErrorBox } from "./error-box";
import { FieldInput } from "./field-input";

/** 云端 api_key 的钥匙链规范名(与 sidecar vision.yaml 引用同口径) */
const SECRET_NAME_IMAGE_API_KEY = "myia/image/api_key";

interface VisionFormProps {
  /** 设置屏已加载的钥匙链名清单(判 api_key 是否已存;值永不可读) */
  secretNames: string[] | null;
}

interface VisionFormState {
  channelDefault: VisionChannel;
  ocrEngine: OcrEngine;
  localBaseUrl: string;
  localModel: string;
  cloudModel: string;
}

function stateFromConfig(config: VisionConfig): VisionFormState {
  return {
    channelDefault: config.channel_default,
    ocrEngine: config.ocr.engine_default,
    localBaseUrl: config.local.base_url,
    localModel: config.local.model,
    cloudModel: config.cloud.model,
  };
}

/** 应答形状防御:mock/异常环境下 invoke 可能回 undefined,不构造即崩。 */
function isVisionConfigLike(value: unknown): value is VisionConfig {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as VisionConfig).channel_default === "string" &&
    typeof (value as VisionConfig).local?.base_url === "string"
  );
}

/**
 * 设置 → 看图分区(两张卡):
 *   1) 结构配置卡:二级看图通道与引擎(经 image.config.save 落
 *      MYIA_HOME/vision.yaml;与三凭据表单不同,本表单结构字段可写回)。
 *      凭据铁律照旧:云端 api_key 只经 secret.set 入钥匙链 myia/image/api_key,
 *      配置里只落 keychain: 引用 —— 明文拒载,值不回显不落盘。
 *   2) 模型管理卡(VisionModelsCard,10-03-vision-v2):本地 MLX 视觉模型
 *      下载/删除/激活 + 本地 mlx_vlm.server 代管行(见下方组件头注释)。
 */
export function VisionForm({ secretNames }: VisionFormProps) {
  const [form, setForm] = useState<VisionFormState>(stateFromConfig(DEFAULT_VISION_CONFIG));
  /** 原样保存 read 应答:save 时保留未入表单的字段(cloud.base_url/ocr.enabled 等) */
  const [loaded, setLoaded] = useState<VisionConfig>(DEFAULT_VISION_CONFIG);
  const [loadedError, setLoadedError] = useState<SidecarRequestError | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [errors, setErrors] = useState<Partial<Record<"localBaseUrl", string>>>({});
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<SidecarRequestError | null>(null);

  useEffect(() => {
    let cancelled = false;
    readImageConfig()
      .then((value) => {
        if (cancelled) return;
        if (!isVisionConfigLike(value)) {
          throw new SidecarRequestError({ code: "transport_error", path: "$", message: "image.config.read 应答形状异常" });
        }
        setLoaded(value);
        setForm(stateFromConfig(value));
      })
      .catch((raw) => {
        if (!cancelled) setLoadedError(raw instanceof SidecarRequestError ? raw : new SidecarRequestError({ code: "transport_error", path: "$", message: String(raw) }));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSave = useCallback(async () => {
    const localBase = form.localBaseUrl.trim() || DEFAULT_VISION_CONFIG.local.base_url;
    if (!/^https?:\/\//.test(localBase)) {
      setErrors({ localBaseUrl: "本地 base_url 须为 http(s) 地址(留空用默认 http://127.0.0.1:8080/v1)" });
      return;
    }
    setErrors({});
    setSaving(true);
    setSaveError(null);
    setStatus(null);
    try {
      // 1) 凭据(如有输入)只入钥匙链;配置里仅落引用
      let apiKeyRef = loaded.cloud.api_key;
      if (apiKey) {
        await saveSecret(SECRET_NAME_IMAGE_API_KEY, apiKey);
        apiKeyRef = `keychain:${SECRET_NAME_IMAGE_API_KEY}`;
      }
      // 2) 结构配置整份写回(未入表单字段按 read 原样保留)
      const config: VisionConfig = {
        channel_default: form.channelDefault,
        local: { base_url: localBase, model: form.localModel.trim() },
        cloud: { base_url: loaded.cloud.base_url, model: form.cloudModel.trim() || loaded.cloud.model, api_key: apiKeyRef },
        ocr: { enabled: loaded.ocr.enabled, engine_default: form.ocrEngine },
      };
      await saveImageConfig(config);
      setApiKey(""); // key 保存即清:不留存、不回显
      // 3) 复核往返:重读配置与所写一致才算数
      const rereadValue = await readImageConfig();
      const consistent =
        isVisionConfigLike(rereadValue) &&
        rereadValue.channel_default === config.channel_default &&
        rereadValue.local.base_url === config.local.base_url &&
        rereadValue.local.model === config.local.model &&
        rereadValue.cloud.model === config.cloud.model &&
        rereadValue.ocr.engine_default === config.ocr.engine_default;
      setStatus(
        consistent
          ? "看图配置已保存(vision.yaml),重读复核一致"
          : "已保存,但重读复核未确认 —— 请检查 sidecar 日志(配置可能被同门校验改写)",
      );
      if (consistent && isVisionConfigLike(rereadValue)) {
        setLoaded(rereadValue);
        setForm(stateFromConfig(rereadValue));
      }
    } catch (raw) {
      setSaveError(raw instanceof SidecarRequestError ? raw : new SidecarRequestError({ code: "transport_error", path: "$", message: String(raw) }));
    } finally {
      setSaving(false);
    }
  }, [apiKey, form, loaded]);

  const keyInKeychain = secretNames?.includes(SECRET_NAME_IMAGE_API_KEY) ?? false;

  return (
    <>
      <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="size-4 text-muted-foreground" />
          看图
        </CardTitle>
        <CardDescription>
          二级看图通道与 OCR 引擎结构配置(落 MYIA_HOME/vision.yaml);云端 api_key 只入钥匙链
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {loadedError ? (
          // 加载失败是注记不是告警(不占 role=alert:设置屏既有用例对 alert
          // 单匹配断言);协议未收编(Python 侧未落地)时如实提示,保存可试
          <div
            role="note"
            className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
            data-testid="vision-config-note"
          >
            现值读取失败(code={loadedError.code}:{loadedError.message});表单按缺省展示。保存将尝试写回
            image.config.save —— 协议未收编(Python 侧未落地)时会得到结构化 method_not_found,如实呈现。
          </div>
        ) : null}
        <div className="grid grid-cols-2 gap-2">
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-xs text-muted-foreground">默认通道</span>
            <Select
              value={form.channelDefault}
              onValueChange={(value) => setForm((prev) => ({ ...prev, channelDefault: value as VisionChannel }))}
            >
              <SelectTrigger aria-label="看图默认通道" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="local">本地(零出网)</SelectItem>
                <SelectItem value="cloud">云端(出网)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-xs text-muted-foreground">OCR 默认引擎</span>
            <Select
              value={form.ocrEngine}
              onValueChange={(value) => setForm((prev) => ({ ...prev, ocrEngine: value as OcrEngine }))}
            >
              <SelectTrigger aria-label="OCR 默认引擎" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="vision">Vision(macOS)</SelectItem>
                <SelectItem value="rapidocr">RapidOCR</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <FieldInput
          label="本地 base_url"
          aria-label="本地 base_url"
          placeholder="http://127.0.0.1:8080/v1(mlx-vlm;LM Studio 为 http://127.0.0.1:1234/v1)"
          value={form.localBaseUrl}
          onChange={(event) => setForm((prev) => ({ ...prev, localBaseUrl: event.target.value }))}
          error={errors.localBaseUrl}
          hint="OpenAI 兼容端点(含 /v1 路径);默认 http://127.0.0.1:8080/v1,留空按默认保存"
        />
        <FieldInput
          label="本地模型路径"
          aria-label="本地模型路径"
          placeholder="~/.myia/models/qwen3-vl-8b-mlx(mlx-vlm 的 model 字段即模型路径)"
          value={form.localModel}
          onChange={(event) => setForm((prev) => ({ ...prev, localModel: event.target.value }))}
          hint="「设置本地模型路径」的落点:本地通道 model 字段 = MLX 模型目录;下载/代管属 v2"
        />
        <FieldInput
          label="云端模型"
          aria-label="云端模型"
          placeholder="glm-4.6v"
          value={form.cloudModel}
          onChange={(event) => setForm((prev) => ({ ...prev, cloudModel: event.target.value }))}
          hint="默认 glm-4.6v(glm-4.5v 错读勿用);留空按已保存值"
        />
        <div className="flex items-end gap-2">
          <div className="min-w-0 flex-1">
            <FieldInput
              label="云端 API Key"
              aria-label="云端 API Key"
              type="password"
              autoComplete="new-password"
              placeholder="输入后才写入;保存即清,永不回显"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              hint={`写入钥匙链 ${SECRET_NAME_IMAGE_API_KEY};未录时云端回落既有 LLM key(myia/llm/api_key)`}
            />
          </div>
          {secretNames !== null ? (
            keyInKeychain ? (
              <Badge variant="ok" className="mb-1.5">key 已在钥匙链</Badge>
            ) : (
              <Badge variant="outline" className="mb-1.5">key 未录(云端回落 LLM key)</Badge>
            )
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => void handleSave()} disabled={saving}>
            <Save className="size-3.5" />
            保存看图配置
          </Button>
          {status ? (
            <span role="status" className="text-xs text-ok" data-testid="vision-save-status">
              {status}
            </span>
          ) : null}
        </div>
        {saveError ? <ErrorBox error={saveError} /> : null}
      </CardContent>
      </Card>
      <VisionModelsCard />
    </>
  );
}

// ---------------------------------------------------------------------------
// 模型管理卡(10-03-vision-v2):已装清单 + 下载(事件流)+ 本地 server 代管行
// ---------------------------------------------------------------------------

/** 进行中的下载 job UI 态(进度事件喂字节;completed 定终态) */
interface DownloadJobState {
  jobId: number;
  repo: string;
  doneBytes: number;
  /** HF 未回报总量时为 null(进度条退化为不定态 + 已下载字节) */
  totalBytes: number | null;
}

/** 字节数 → 人类可读(B/KB/MB/GB/TB;一位小数,百级以上取整) */
export function formatModelBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 100 || unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

/**
 * 设置 → 看图分区 · 模型管理卡:本地 MLX 视觉模型(MYIA_HOME/models)的
 * 下载(HF snapshot_download,断点续传)/ 删除 / 激活,与本地 mlx_vlm.server
 * 代管状态行。数据面 = image.models.* / image.server.* 六方法 + 下载域两事件
 * (image.models.progress / completed,经 onSidecarEvent 订阅)。
 */
export function VisionModelsCard() {
  const [models, setModels] = useState<VisionModelEntry[] | null>(null);
  const [listError, setListError] = useState<SidecarRequestError | null>(null);
  /** 下载表单与 job 态(单飞:后端 download_busy,前端按钮随 job 态禁用) */
  const [repoId, setRepoId] = useState("");
  const [localName, setLocalName] = useState("");
  const [job, setJob] = useState<DownloadJobState | null>(null);
  /** completed ok=false 的结构化 code(前端预校验失败也走这里) */
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloadStatus, setDownloadStatus] = useState<string | null>(null);
  /** 删除二次确认(inline confirm,同钥匙链凭据删除惯例)与变更错误 */
  const [deletingModel, setDeletingModel] = useState<string | null>(null);
  const [activating, setActivating] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<SidecarRequestError | null>(null);
  const [activateStatus, setActivateStatus] = useState<string | null>(null);
  /** 服务行:挂载拉一次 status;ensure 是同步长应答(≤120s) */
  const [server, setServer] = useState<ImageServerStatusResult | null>(null);
  const [serverError, setServerError] = useState<SidecarRequestError | null>(null);
  const [ensuring, setEnsuring] = useState(false);
  const [ensureStatus, setEnsureStatus] = useState<string | null>(null);
  const [ensureError, setEnsureError] = useState<SidecarRequestError | null>(null);
  /** 订阅期防陈旧闭包:job id 走 ref 过滤(事件只喂本会话发起的 job) */
  const jobIdRef = useRef<number | null>(null);

  const refreshModels = useCallback(async () => {
    setListError(null);
    try {
      setModels(await listImageModels());
    } catch (raw) {
      setListError(raw instanceof SidecarRequestError ? raw : new SidecarRequestError({ code: "transport_error", path: "$", message: String(raw) }));
    }
  }, []);

  const refreshServer = useCallback(async () => {
    setServerError(null);
    try {
      setServer(await imageServerStatus());
    } catch (raw) {
      setServerError(raw instanceof SidecarRequestError ? raw : new SidecarRequestError({ code: "transport_error", path: "$", message: String(raw) }));
    }
  }, []);

  useEffect(() => {
    void refreshModels();
    void refreshServer();
  }, [refreshModels, refreshServer]);

  // 下载域事件订阅(progress 喂进度;completed 定终态 → 成功刷新清单/失败出 error)。
  // catch:浏览器直开/壳未起时 listen 不可用 —— 进度条退化为提交即等待态,不炸卡。
  useEffect(() => {
    let unlisten: UnlistenFn | null = null;
    let cancelled = false;
    void onSidecarEvent((event) => {
      if (event.type === "image.models.progress") {
        if (jobIdRef.current !== event.job_id) return;
        setJob((prev) =>
          prev && prev.jobId === event.job_id
            ? { ...prev, doneBytes: event.done_bytes, totalBytes: event.total_bytes ?? prev.totalBytes }
            : prev,
        );
      } else if (event.type === "image.models.completed") {
        if (jobIdRef.current !== event.job_id) return;
        jobIdRef.current = null;
        if (event.ok) {
          setJob(null);
          setDownloadStatus("模型已下载完成,清单已刷新(可点「设为当前」激活)");
          void refreshModels();
        } else {
          setJob(null);
          setDownloadError(event.error ?? "未知错误");
        }
      }
    })
      .then((un) => {
        if (cancelled) un();
        else unlisten = un;
      })
      .catch(() => {
        // 订阅通道不可用:保留 job 态(completed 不可达时用户可刷新清单自行收口)
      });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [refreshModels]);

  const handleDownload = useCallback(async () => {
    const repo = repoId.trim();
    if (!IMAGE_MODELS_REPO_RE.test(repo)) {
      setDownloadError("repo 须为 mlx-community/<name> 形式(MLX 格式权重直下免 convert)");
      return;
    }
    const name = localName.trim();
    if (name && !IMAGE_MODELS_NAME_RE.test(name)) {
      setDownloadError("本地名须以字母/数字开头,仅含 . _ -(禁路径分隔)");
      return;
    }
    setDownloadError(null);
    setDownloadStatus(null);
    try {
      const result = await downloadImageModel({ repo, ...(name ? { name } : {}) });
      jobIdRef.current = result.job_id;
      setJob({ jobId: result.job_id, repo, doneBytes: 0, totalBytes: null });
    } catch (raw) {
      const failure = raw instanceof SidecarRequestError ? raw : new SidecarRequestError({ code: "transport_error", path: "$", message: String(raw) });
      setDownloadError(`${failure.code}: ${failure.message}`);
    }
  }, [repoId, localName]);

  const handleDelete = useCallback(
    async (name: string) => {
      setMutationError(null);
      try {
        await deleteImageModel({ name });
        setDeletingModel(null);
        await refreshModels();
      } catch (raw) {
        setMutationError(raw instanceof SidecarRequestError ? raw : new SidecarRequestError({ code: "transport_error", path: "$", message: String(raw) }));
      }
    },
    [refreshModels],
  );

  const handleActivate = useCallback(
    async (name: string) => {
      setMutationError(null);
      setActivateStatus(null);
      setActivating(name);
      try {
        await activateImageModel({ name });
        setActivateStatus(`已设为当前模型:${name}(vision.yaml local.model 已改写)`);
        await refreshModels();
      } catch (raw) {
        setMutationError(raw instanceof SidecarRequestError ? raw : new SidecarRequestError({ code: "transport_error", path: "$", message: String(raw) }));
      } finally {
        setActivating(null);
      }
    },
    [refreshModels],
  );

  /** ensure 是同步长应答(健康等待 ≤120s):按钮禁用 + 文案明示约 2 分钟 */
  const handleEnsure = useCallback(async () => {
    setEnsuring(true);
    setEnsureError(null);
    setEnsureStatus(null);
    try {
      const result = await ensureImageServer();
      setServer(result);
      setEnsureStatus(
        result.started
          ? `本地 server 已自起并达健康(${result.base_url};Metal JIT 首载最长约 2 分钟)`
          : `本地 server 已在运行(${result.base_url}),无需自起`,
      );
    } catch (raw) {
      setEnsureError(raw instanceof SidecarRequestError ? raw : new SidecarRequestError({ code: "transport_error", path: "$", message: String(raw) }));
    } finally {
      setEnsuring(false);
    }
  }, []);

  const percent = job && job.totalBytes ? Math.min(100, (job.doneBytes / job.totalBytes) * 100) : null;

  return (
    <Card data-testid="vision-models-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HardDrive className="size-4 text-muted-foreground" />
          看图模型管理
        </CardTitle>
        <CardDescription>
          本地 MLX 视觉模型(MYIA_HOME/models):下载 / 删除 / 激活 + 本地 mlx_vlm.server 代管
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {/* 挂载期读取失败 = 注记不是告警(同上方 config 卡惯例:不占 role=alert,
            设置屏既有用例对 alert 单匹配断言);动作期错误才走 ErrorBox */}
        {listError ? (
          <div
            role="note"
            className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
            data-testid="vision-models-note"
          >
            已装清单读取失败(code={listError.code}:{listError.message});协议未收编
            (Python 侧未落地)时如实呈现,下载/删除/激活可试。
          </div>
        ) : null}
        {mutationError ? <ErrorBox error={mutationError} /> : null}
        {models === null && listError === null ? (
          <span className="text-xs text-muted-foreground">已装清单加载中…</span>
        ) : models !== null && models.length === 0 ? (
          <span className="text-xs text-muted-foreground" data-testid="vision-models-empty">
            尚未安装任何模型(models/ 为空);在下方下载第一个
          </span>
        ) : (
          <div className="flex flex-col gap-1.5" data-testid="vision-models-list">
            {models?.map((model) => (
              <div
                key={model.name}
                data-testid={`vision-model-${model.name}`}
                className="flex flex-wrap items-center gap-1.5 rounded-md border border-border/60 px-2.5 py-1.5"
              >
                <span className="min-w-0 truncate font-mono text-xs" title={model.path}>
                  {model.name}
                </span>
                <span className="text-[11px] text-muted-foreground">{formatModelBytes(model.bytes)}</span>
                {model.active ? (
                  <Badge variant="ok">当前</Badge>
                ) : (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 px-1.5 text-xs"
                    onClick={() => void handleActivate(model.name)}
                    disabled={activating !== null}
                  >
                    {activating === model.name ? "激活中…" : "设为当前"}
                  </Button>
                )}
                <span className="ml-auto flex items-center gap-0.5">
                  {deletingModel === model.name ? (
                    <>
                      <Button
                        size="sm"
                        variant="destructive"
                        data-testid={`vision-model-delete-confirm-${model.name}`}
                        onClick={() => void handleDelete(model.name)}
                      >
                        确认删除
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setDeletingModel(null)}>
                        取消
                      </Button>
                    </>
                  ) : (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      aria-label={`删除模型 ${model.name}`}
                      disabled={model.active}
                      title={
                        model.active
                          ? "当前模型不可删除(后端 model_active_refused);先激活别的模型"
                          : `删除 ${model.name}(${formatModelBytes(model.bytes)})`
                      }
                      onClick={() => {
                        setMutationError(null);
                        setDeletingModel(model.name);
                      }}
                    >
                      <Trash2 className="size-3" />
                    </Button>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}
        {activateStatus ? (
          <p role="status" className="text-xs text-ok" data-testid="vision-activate-status">
            {activateStatus}
          </p>
        ) : null}

        {/* 下载区:repo_id + 本地名(可选);进度条吃 progress 事件 */}
        <div className="flex flex-col gap-2 border-t border-border/60 pt-3">
          <div className="grid grid-cols-2 gap-2">
            <FieldInput
              label="repo_id"
              aria-label="模型 repo_id"
              placeholder="mlx-community/Qwen2.5-VL-7B-Instruct-4bit"
              value={repoId}
              onChange={(event) => setRepoId(event.target.value)}
              hint="HF 仓库全名;须 mlx-community/<name>(MLX 权重直下免 convert)"
            />
            <FieldInput
              label="本地名(可选)"
              aria-label="模型本地名"
              placeholder="Qwen2.5-VL-7B-Instruct-4bit(缺省 = repo 名段)"
              value={localName}
              onChange={(event) => setLocalName(event.target.value)}
              hint="装到 MYIA_HOME/models/<本地名>;断点续传,失败可重下"
            />
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => void handleDownload()} disabled={job !== null}>
              <Download className="size-3.5" />
              {job ? "下载中…" : "下载模型"}
            </Button>
            <span className="text-[11px] text-muted-foreground">磁盘预检不足会在完成事件收口 disk_insufficient</span>
          </div>
          {job ? (
            <div className="flex flex-col gap-1" data-testid="vision-download-progress">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className={cn(
                    "h-full rounded-full transition-all",
                    percent !== null ? "bg-primary" : "w-full animate-pulse bg-primary/50",
                  )}
                  style={percent !== null ? { width: `${percent.toFixed(1)}%` } : undefined}
                  role="progressbar"
                  aria-valuenow={percent !== null ? Math.round(percent) : undefined}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`下载进度 ${job.repo}`}
                />
              </div>
              <span className="text-[11px] text-muted-foreground">
                {job.repo}:
                {percent !== null
                  ? ` ${formatModelBytes(job.doneBytes)} / ${formatModelBytes(job.totalBytes ?? 0)}(${percent.toFixed(0)}%)`
                  : ` 已下载 ${formatModelBytes(job.doneBytes)}(总量未知)`}
              </span>
            </div>
          ) : null}
          {downloadStatus ? (
            <p role="status" className="text-xs text-ok" data-testid="vision-download-status">
              {downloadStatus}
            </p>
          ) : null}
          {downloadError ? (
            <p role="alert" className="text-xs text-destructive" data-testid="vision-download-error">
              下载失败:{downloadError}
            </p>
          ) : null}
        </div>

        {/* 服务行:status 挂载拉一次;ensure 同步等健康(最长约 2 分钟) */}
        <div className="flex flex-col gap-2 border-t border-border/60 pt-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">本地服务</span>
            {server === null ? (
              serverError ? (
                <Badge variant="unknown" data-testid="vision-server-badge">
                  状态未知
                </Badge>
              ) : (
                <span className="text-xs text-muted-foreground">探测中…</span>
              )
            ) : server.healthy ? (
              <Badge variant="ok" data-testid="vision-server-badge">
                运行中 · 健康
              </Badge>
            ) : server.running ? (
              <Badge variant="warning" data-testid="vision-server-badge">
                已监听 · /models 异常
              </Badge>
            ) : (
              <Badge variant="destructive" data-testid="vision-server-badge">
                未运行
              </Badge>
            )}
            <Button size="sm" variant="outline" onClick={() => void handleEnsure()} disabled={ensuring}>
              {ensuring ? "确保启动中…(最长约 2 分钟)" : "确保启动"}
            </Button>
            <Button size="sm" variant="ghost" aria-label="刷新服务状态" onClick={() => void refreshServer()} disabled={ensuring}>
              刷新
            </Button>
          </div>
          {server ? (
            <p className="text-[11px] text-muted-foreground">
              {server.base_url} · 模型 {server.model || "(未配置)"}
            </p>
          ) : null}
          {serverError ? (
            <div
              role="note"
              className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
              data-testid="vision-server-note"
            >
              服务状态探测失败(code={serverError.code}:{serverError.message});「确保启动」仍可尝试。
            </div>
          ) : null}
          {ensureStatus ? (
            <p role="status" className="text-xs text-ok" data-testid="vision-ensure-status">
              {ensureStatus}
            </p>
          ) : null}
          {ensureError ? <ErrorBox error={ensureError} onRetry={() => void handleEnsure()} retrying={ensuring} /> : null}
        </div>
      </CardContent>
    </Card>
  );
}
