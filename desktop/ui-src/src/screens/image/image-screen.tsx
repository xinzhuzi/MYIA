import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { SidecarRequestError } from "@/lib/api";
import type { AnalyzeMode, OcrEngine, VisionChannel, VisionConfig } from "@/lib/api";

import {
  asSidecarError,
  DEFAULT_VISION_CONFIG,
  importImageByBase64,
  importImageByPath,
  basename,
  readFileAsDataUrl,
  readImageConfig,
  validateImageFile,
} from "./api";
import { DropZone } from "./drop-zone";
import type { ImportedImage } from "./thumb";
import { Thumb } from "./thumb";
import { OcrPanel } from "./ocr-panel";
import { VisionPanel } from "./vision-panel";

/**
 * 看图:情报分析工具屏(五屏无输入框的例外 —— 图片即输入,v1 单图)。
 *
 * 链路:拖/贴/选 → image.import(落库 MYIA_HOME/images,sha256 去重)→
 * 自动一级 OCR(双引擎,逐行置信度)→ 手动二级看图(本地/云端,事件流)。
 * 结果仅屏上展示 + 文本可复制,不入库不持久化(prd 拍板)。
 */
export function ImageScreen() {
  const [configError, setConfigError] = useState<SidecarRequestError | null>(null);
  const [image, setImage] = useState<ImportedImage | null>(null);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<SidecarRequestError | null>(null);
  const [engine, setEngine] = useState<OcrEngine>(DEFAULT_VISION_CONFIG.ocr.engine_default);
  const [mode, setMode] = useState<AnalyzeMode>("read");
  const [channel, setChannel] = useState<VisionChannel>(DEFAULT_VISION_CONFIG.channel_default);
  /** 低置信升二级信号(自增 = 按当前 mode 启动二级看图) */
  const [startSignal, setStartSignal] = useState(0);

  // 配置加载:引擎/通道默认值随之落位;失败(含协议未收编的 method_not_found)
  // 不拦主流程 —— 回退缺省(引擎 vision / 通道本地),错误以注记呈现
  useEffect(() => {
    let cancelled = false;
    readImageConfig()
      .then((loaded: VisionConfig) => {
        if (cancelled) return;
        setEngine(loaded.ocr.engine_default);
        setChannel(loaded.channel_default);
      })
      .catch((raw) => {
        if (!cancelled) setConfigError(asSidecarError(raw));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** 拖/贴入口:前端预检(格式/大小)→ base64 导入(缩略图 data: URL 同源可得) */
  const handleFile = useCallback(async (file: File) => {
    const invalid = validateImageFile(file);
    if (invalid) {
      setImportError(new SidecarRequestError({ code: "image_unsupported", path: "$", message: invalid }));
      return;
    }
    setImporting(true);
    setImportError(null);
    try {
      const { dataUrl, base64, mime } = await readFileAsDataUrl(file);
      const imported = await importImageByBase64(base64, mime);
      setImage({ ...imported, name: file.name, previewUrl: dataUrl });
    } catch (raw) {
      setImportError(asSidecarError(raw));
    } finally {
      setImporting(false);
    }
  }, []);

  /** 选择器/Tauri 拖放入口:路径导入(webview 无文件内容,无缩略图) */
  const handlePath = useCallback(async (path: string) => {
    setImporting(true);
    setImportError(null);
    try {
      const imported = await importImageByPath(path);
      setImage({ ...imported, name: basename(path), previewUrl: null });
    } catch (raw) {
      setImportError(asSidecarError(raw));
    } finally {
      setImporting(false);
    }
  }, []);

  /** 低置信升二级:切 read 模式并直接启动(按钮点击即手动动作,不自动) */
  const escalate = useCallback(() => {
    setMode("read");
    setStartSignal((n) => n + 1);
  }, []);

  return (
    <div className="flex flex-col gap-4 pb-6">
      <PageHeader
        title="看图"
        description="图片即输入:入库去重 → 一级 OCR 双引擎逐行提字 → 二级本地/云端视觉解读;结果不入库"
        actions={<span className="text-[11px] text-muted-foreground">v1 单图 · 多图 v2</span>}
      />

      {configError ? (
        <div className="px-6">
          <div role="note" className="rounded-md border border-border bg-muted/30 px-4 py-3 text-xs text-muted-foreground">
            看图配置读取失败(code={configError.code}:{configError.message});已按缺省工作 —— 引擎 vision / 通道本地。设置见「设置 → 看图」。
          </div>
        </div>
      ) : null}
      {importError ? (
        <div className="px-6">
          <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm">
            <p className="font-medium text-destructive">{importError.message}</p>
            <p className="mt-1 font-mono text-xs text-muted-foreground">
              code={importError.code} path={importError.path}
            </p>
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 px-6 xl:grid-cols-2">
        {/* 左列:图片进入 + 缩略图 + 一级 OCR */}
        <div className="flex flex-col gap-3">
          {image ? (
            <Thumb image={image} onClear={() => setImage(null)} />
          ) : (
            <Card>
              <CardContent className="p-3">
                <DropZone
                  disabled={importing}
                  onPath={(path) => void handlePath(path)}
                  onFile={(file) => void handleFile(file)}
                  onError={setImportError}
                />
              </CardContent>
            </Card>
          )}
          <OcrPanel imageId={image?.id ?? null} engine={engine} onEngineChange={setEngine} onEscalate={escalate} />
        </div>

        {/* 右列:二级看图 */}
        <VisionPanel
          imageId={image?.id ?? null}
          mode={mode}
          onModeChange={setMode}
          channel={channel}
          onChannelChange={setChannel}
          startSignal={startSignal}
        />
      </div>

      <p className="px-6 text-[11px] text-muted-foreground">
        隐私边界:本地通道零出网(断网可用);云端通道首次切换弹知情确认。图片落本机数据根(MYIA_HOME/images,sha256
        去重);解读结果仅屏上展示,不持久化。
      </p>
    </div>
  );
}
