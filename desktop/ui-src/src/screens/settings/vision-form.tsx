import { KeyRound, Save } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SidecarRequestError } from "@/lib/api";
import type { OcrEngine, VisionChannel, VisionConfig } from "@/lib/api";

import { DEFAULT_VISION_CONFIG, readImageConfig, saveImageConfig } from "@/screens/image/api";

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
 * 设置 → 看图分区:二级看图通道与引擎的结构配置(经 image.config.save 落
 * MYIA_HOME/vision.yaml;与三凭据表单不同,本表单结构字段可写回)。
 * 凭据铁律照旧:云端 api_key 只经 secret.set 入钥匙链 myia/image/api_key,
 * 配置里只落 keychain: 引用 —— 明文拒载,值不回显不落盘。
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
      setErrors({ localBaseUrl: "本地 base_url 须为 http(s) 地址(留空用默认 http://127.0.0.1:8080)" });
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
          placeholder="http://127.0.0.1:8080(mlx-vlm;LM Studio 为 http://127.0.0.1:1234)"
          value={form.localBaseUrl}
          onChange={(event) => setForm((prev) => ({ ...prev, localBaseUrl: event.target.value }))}
          error={errors.localBaseUrl}
          hint="OpenAI 兼容端点;默认 http://127.0.0.1:8080,留空按默认保存"
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
              hint={`写入钥匙链 ${SECRET_NAME_IMAGE_API_KEY};vision.yaml 只存 keychain: 引用`}
            />
          </div>
          {secretNames !== null ? (
            keyInKeychain ? (
              <Badge variant="ok" className="mb-1.5">key 已在钥匙链</Badge>
            ) : (
              <Badge variant="outline" className="mb-1.5">key 未录</Badge>
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
  );
}
