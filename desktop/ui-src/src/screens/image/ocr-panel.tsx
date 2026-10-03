import { AlertTriangle, ArrowUpNarrowWide, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { SidecarRequestError } from "@/lib/api";
import type { OcrEngine, ImageOcrResult } from "@/lib/api";

import { asSidecarError, confTone, runImageOcr } from "./api";

const ENGINES: { key: OcrEngine; label: string; note: string }[] = [
  { key: "vision", label: "Vision", note: "macOS Vision(ocrmac):毫秒级,置信度 0.30-1.0 真实分布" },
  { key: "rapidocr", label: "RapidOCR", note: "onnxruntime 内置模型:零下载,分数普遍 ≥0.9(刻度偏高,不与 Vision 互比)" },
];

interface OcrPanelProps {
  /** 无图(idle 态);换图即自动重跑(已拍板:选图自动触发) */
  imageId: string | null;
  engine: OcrEngine;
  /** 引擎切换(切换即重跑由本面板 effect 承担;screen 只持状态) */
  onEngineChange: (engine: OcrEngine) => void;
  /** 低置信手动升二级(仅提示不自动,已拍板) */
  onEscalate: () => void;
}

type OcrPhase =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "error"; error: SidecarRequestError }
  | { phase: "done"; result: ImageOcrResult };

/**
 * 一级 OCR:双引擎分段控件(Vision/RapidOCR,切换即重跑)+ 逐行置信度色阶
 * (≤0.5 红 / ≤0.9 琥珀 / 其余绿;阈值两引擎统一)+ 低置信警示与手动「升二级看图」。
 * 结果标注来源引擎与耗时;错误(image_ocr_failed 等)结构化如实展示。
 */
export function OcrPanel({ imageId, engine, onEngineChange, onEscalate }: OcrPanelProps) {
  const [state, setState] = useState<OcrPhase>({ phase: "idle" });
  /** 手动重试幂等键:同图同引擎重跑 */
  const [attempt, setAttempt] = useState(0);

  const run = useCallback(
    async (id: string, engineArg: OcrEngine) => {
      setState({ phase: "loading" });
      try {
        setState({ phase: "done", result: await runImageOcr(id, engineArg) });
      } catch (raw) {
        setState({ phase: "error", error: asSidecarError(raw) });
      }
    },
    [],
  );

  // 自动触发:选图后自动跑(拍板);引擎切换即重跑(10-03 修订)
  useEffect(() => {
    if (!imageId) {
      setState({ phase: "idle" });
      return;
    }
    void run(imageId, engine);
  }, [imageId, engine, attempt, run]);

  const result = state.phase === "done" ? state.result : null;
  const lowConfCount = result ? result.lines.filter((line) => confTone(line.conf) === "low").length : 0;
  const engineNote = ENGINES.find((entry) => entry.key === engine)?.note ?? "";

  return (
    <Card>
      <CardHeader>
        <CardTitle>一级 OCR(逐行提字)</CardTitle>
        <CardDescription>选图自动触发;引擎切换即重跑。{engineNote}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <div role="tablist" aria-label="OCR 引擎" className="flex items-center gap-1">
            {ENGINES.map((entry) => (
              <Button
                key={entry.key}
                role="tab"
                size="sm"
                variant={engine === entry.key ? "secondary" : "ghost"}
                aria-selected={engine === entry.key}
                onClick={() => onEngineChange(entry.key)}
                disabled={!imageId && state.phase === "idle"}
              >
                {entry.label}
              </Button>
            ))}
          </div>
          {result ? (
            <span className="text-[11px] text-muted-foreground" data-testid="ocr-meta">
              来源 {result.engine} · {result.lines.length} 行 · {result.ms} ms
            </span>
          ) : null}
        </div>

        {state.phase === "idle" ? (
          <p className="rounded-md border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground">
            导入图片后自动逐行提字(带置信度)
          </p>
        ) : state.phase === "loading" ? (
          <div className="flex items-center gap-2 px-1 py-6 text-sm text-muted-foreground" data-testid="ocr-loading">
            <RefreshCw className="size-3.5 animate-spin" />
            {engine === "vision" ? "Vision" : "RapidOCR"} 识别中…
          </div>
        ) : state.phase === "error" ? (
          <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm">
            <p className="flex items-center gap-1.5 font-medium text-destructive">
              <AlertTriangle className="size-4" />
              OCR 失败:{state.error.message}
            </p>
            <p className="mt-1 font-mono text-xs text-muted-foreground">code={state.error.code} path={state.error.path}</p>
            <Button variant="outline" size="sm" className="mt-2" onClick={() => setAttempt((n) => n + 1)}>
              重试
            </Button>
          </div>
        ) : result && result.lines.length === 0 ? (
          <p className="rounded-md border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground">
            未识别到文字(无文字不是终点:用下方「图像描述」看图)
          </p>
        ) : result ? (
          <div className="flex flex-col gap-1.5">
            {lowConfCount > 0 ? (
              <div
                role="status"
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning"
                data-testid="ocr-lowconf"
              >
                <span className="flex items-center gap-1.5">
                  <AlertTriangle className="size-3.5" />
                  {lowConfCount} 行置信度 ≤ 0.5(识别存疑,数字/ID 请逐位核对)
                </span>
                <Button size="sm" variant="outline" onClick={onEscalate} data-testid="ocr-escalate">
                  <ArrowUpNarrowWide className="size-3.5" />
                  升二级看图
                </Button>
              </div>
            ) : null}
            <ol className="flex max-h-72 flex-col gap-1 overflow-y-auto pr-1" data-testid="ocr-lines">
              {result.lines.map((line, index) => {
                const tone = confTone(line.conf);
                return (
                  <li
                    key={`${index}-${line.text}`}
                    data-testid="ocr-line"
                    data-conf-tone={tone}
                    className="flex items-start justify-between gap-2 rounded-sm border border-border/60 bg-muted/10 px-2 py-1"
                  >
                    <span className="min-w-0 flex-1 break-all font-mono text-xs leading-relaxed text-foreground">
                      {line.text}
                    </span>
                    <span className="flex shrink-0 items-center gap-1">
                      {tone === "low" ? <AlertTriangle className="size-3 text-destructive" aria-label="低置信警示" /> : null}
                      <Badge
                        variant={tone === "low" ? "destructive" : tone === "mid" ? "warning" : "ok"}
                        className="font-mono"
                        title={tone === "low" ? "置信度 ≤ 0.5" : tone === "mid" ? "置信度 0.5-0.9" : "置信度 > 0.9"}
                      >
                        {line.conf.toFixed(2)}
                      </Badge>
                    </span>
                  </li>
                );
              })}
            </ol>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
