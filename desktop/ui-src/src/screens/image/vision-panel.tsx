import { AlertTriangle, Cloud, Eye, Loader2, Server } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { onSidecarEvent, SidecarRequestError } from "@/lib/api";
import type {
  AnalyzeMode,
  ImageAnalyzeOutcome,
  ImageCompletedEvent,
  SidecarErrorShape,
  UnlistenFn,
  VisionChannel,
} from "@/lib/api";

import { asSidecarError, copyText, hasCloudConsent, imageStatus, rememberCloudConsent, startImageAnalyze } from "./api";

const MODES: { key: AnalyzeMode; label: string; hint: string }[] = [
  { key: "read", label: "读字校对", hint: "OCR 初稿嵌校对 prompt,对照图片逐行修正确有出入的字" },
  { key: "describe", label: "图像描述", hint: "主体/构图/风格色彩/氛围/是否疑似 AI 生成" },
  { key: "ask", label: "自由提问", hint: "对图片提任意问题" },
];

interface VisionPanelProps {
  imageId: string | null;
  mode: AnalyzeMode;
  onModeChange: (mode: AnalyzeMode) => void;
  channel: VisionChannel;
  onChannelChange: (channel: VisionChannel) => void;
  /** 外部触发启动(低置信升二级):自增即按当前 mode 启动 */
  startSignal: number;
}

type JobState =
  | { phase: "idle" }
  | { phase: "starting" }
  | { phase: "running"; jobId: number; stage?: string; pct?: number }
  | { phase: "done"; outcome: ImageAnalyzeOutcome }
  | { phase: "error"; error: SidecarRequestError };

const STAGE_TEXT: Record<string, string> = {
  ocr: "一级 OCR 初稿",
  model: "视觉模型解读中(本地 13-33s 属正常,首请求含编译更久)",
};

/**
 * 二级看图:三模式(read/describe/ask)+ 本地/云端通道 + 事件流进行态。
 *
 * 事件流(设计定案):image.analyze 立即返回 job_id,结果走 image.progress /
 * image.completed 事件 —— 规避壳单请求 120s 硬超时(VL 调用 13-33s + Metal JIT)。
 *
 * 隐私(拍板):本地通道零出网;切云端首次弹「图片内容将出网」知情确认并
 * 本地记住;结果仅屏上展示 + 文本可复制,不入库不持久化。
 */
export function VisionPanel({ imageId, mode, onModeChange, channel, onChannelChange, startSignal }: VisionPanelProps) {
  const [question, setQuestion] = useState("");
  const [job, setJob] = useState<JobState>({ phase: "idle" });
  /** 云端知情确认:true 时渲染确认框(区分触发来源:切通道 / 点启动) */
  const [consentFor, setConsentFor] = useState<"switch" | "start" | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "ok" | "manual">("idle");
  /** 已知情标记的镜像(确认后立即生效,不必读回 localStorage) */
  const consentRef = useRef(hasCloudConsent());
  const resultTextRef = useRef<HTMLParagraphElement | null>(null);

  const busy = job.phase === "starting" || job.phase === "running";

  const launch = useCallback(async () => {
    if (!imageId) return;
    setJob({ phase: "starting" });
    setCopyState("idle");
    try {
      const started = await startImageAnalyze({
        id: imageId,
        mode,
        ...(mode === "ask" && question.trim() ? { question: question.trim() } : {}),
        channel,
      });
      setJob({ phase: "running", jobId: started.job_id });
    } catch (raw) {
      setJob({ phase: "error", error: asSidecarError(raw) });
    }
  }, [channel, imageId, mode, question]);

  /** 通道切换:云端且未知情 → 拦下先确认;本地直切。 */
  const handleChannelSwitch = useCallback(
    (next: VisionChannel) => {
      if (next === "cloud" && channel !== "cloud" && !consentRef.current) {
        setConsentFor("switch");
        return;
      }
      onChannelChange(next);
    },
    [channel, onChannelChange],
  );

  const handleStart = useCallback(() => {
    if (busy) return;
    if (mode === "ask" && !question.trim()) return; // 问题必填(前端先挡)
    if (channel === "cloud" && !consentRef.current) {
      setConsentFor("start");
      return;
    }
    void launch();
  }, [busy, channel, launch, mode, question]);

  /** 低置信升二级信号:mode 已由 screen 切到 read,这里直接启动。
   *  最新闭包经 ref 转发:效果只依赖信号本身,不随 mode/question 变化重触发。 */
  const startSignalRef = useRef(startSignal);
  const launchRef = useRef(launch);
  launchRef.current = launch;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const imageIdRef = useRef(imageId);
  imageIdRef.current = imageId;
  useEffect(() => {
    if (startSignal === startSignalRef.current) return;
    startSignalRef.current = startSignal;
    if (!busyRef.current && imageIdRef.current) void launchRef.current();
  }, [startSignal]);

  // 事件流订阅:running 期间监听 progress/completed(jobId 精确匹配)
  const jobPhase = job.phase;
  const jobId = job.phase === "running" ? job.jobId : null;
  useEffect(() => {
    if (jobPhase !== "running" || jobId === null) return;
    let unlisten: UnlistenFn | null = null;
    let cancelled = false;
    const applyCompleted = (event: ImageCompletedEvent) => {
      if (event.ok && event.result) {
        setJob({ phase: "done", outcome: event.result });
      } else {
        const shape = (event.error ?? undefined) as SidecarErrorShape | undefined;
        setJob({
          phase: "error",
          error: shape
            ? new SidecarRequestError(shape)
            : new SidecarRequestError({ code: "image_provider_error", path: "$", message: "看图任务失败(事件未携带错误明细)" }),
        });
      }
    };
    void onSidecarEvent((event) => {
      if (event.type === "image.progress" && event.job_id === jobId) {
        setJob((current) =>
          current.phase === "running" ? { ...current, stage: event.stage, pct: event.pct } : current,
        );
      } else if (event.type === "image.completed" && event.job_id === jobId) {
        applyCompleted(event);
      }
    })
      .then(async (un) => {
        if (cancelled) {
          un();
          return;
        }
        unlisten = un;
        // 对账:瞬时失败任务的 completed 可能在订阅建立前写出而被丢(壳转发无
        // 重放)—— 订阅就绪后按 job_id 拉 image.status,最近终态即本任务应见的
        // 那份(design:image.status UI 对账),面板不永久卡「进行中」
        try {
          const status = await imageStatus(jobId);
          if (!cancelled && status.last && status.last.job_id === jobId) {
            applyCompleted(status.last);
          }
        } catch {
          // 对账不可达:保持事件流路径(completed 正常仍经事件抵达)
        }
      })
      .catch(() => {
        // 订阅不可用:completed 不会到达;保持 running 态如实可见(用户可换通道重试)
      });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [jobPhase, jobId]);

  const handleCopy = useCallback(async () => {
    if (job.phase !== "done") return;
    const ok = await copyText(job.outcome.text);
    if (ok) {
      setCopyState("ok");
    } else {
      setCopyState("manual");
      // 退化:全选结果文本,用户 Cmd+C 手动复制
      const node = resultTextRef.current;
      if (node) {
        const range = document.createRange();
        range.selectNodeContents(node);
        const selection = window.getSelection();
        selection?.removeAllRanges();
        selection?.addRange(range);
      }
    }
  }, [job]);

  const consentConfirm = useCallback(() => {
    rememberCloudConsent();
    consentRef.current = true;
    setConsentFor(null);
    onChannelChange("cloud");
    if (consentFor === "start") void launch();
  }, [consentFor, launch, onChannelChange]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>二级看图(本地 / 云端视觉模型)</CardTitle>
        <CardDescription>事件流后台任务:启动即返回,进度与结果经事件抵达(不受壳 120s 超时限制)</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {/* 模式三选 */}
        <div className="flex flex-wrap items-center gap-1" role="tablist" aria-label="看图模式">
          {MODES.map((entry) => (
            <Button
              key={entry.key}
              role="tab"
              size="sm"
              variant={mode === entry.key ? "secondary" : "ghost"}
              aria-selected={mode === entry.key}
              onClick={() => onModeChange(entry.key)}
              disabled={busy}
            >
              {entry.label}
            </Button>
          ))}
        </div>
        <p className="text-[11px] text-muted-foreground">{MODES.find((entry) => entry.key === mode)?.hint}</p>

        {mode === "ask" ? (
          <input
            aria-label="提问"
            placeholder="对这张图问点什么(必填)"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            disabled={busy}
            className="h-8 w-full rounded-md border border-input bg-transparent px-2.5 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-[3px] focus-visible:ring-ring/40"
          />
        ) : null}

        {/* 通道切换 */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1" role="tablist" aria-label="看图通道">
            <Button
              role="tab"
              size="sm"
              variant={channel === "local" ? "secondary" : "ghost"}
              aria-selected={channel === "local"}
              onClick={() => handleChannelSwitch("local")}
              disabled={busy}
            >
              <Server className="size-3.5" />
              本地
            </Button>
            <Button
              role="tab"
              size="sm"
              variant={channel === "cloud" ? "secondary" : "ghost"}
              aria-selected={channel === "cloud"}
              onClick={() => handleChannelSwitch("cloud")}
              disabled={busy}
            >
              <Cloud className="size-3.5" />
              云端
            </Button>
          </div>
          <span className="text-[11px] text-muted-foreground">
            {channel === "local"
              ? "本地通道零出网(断网可用);端点与模型路径在设置 → 看图"
              : "云端通道:图片内容将出网;api_key 只入钥匙链"}
          </span>
        </div>

        {/* 云端知情确认(首次;记住选择) */}
        {consentFor !== null ? (
          <div
            role="alertdialog"
            aria-label="云端出网知情确认"
            data-testid="cloud-consent"
            className="flex flex-col gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-3 text-sm"
          >
            <p className="flex items-center gap-1.5 font-medium text-warning">
              <AlertTriangle className="size-4" />
              图片内容将出网
            </p>
            <p className="text-xs text-muted-foreground">
              云端通道会把整张图片发送到所配置的视觉 API(bigmodel 等)进行解读。本地通道不发生任何网络请求。确认后将记住本次选择,不再重复询问。
            </p>
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={consentConfirm} data-testid="cloud-consent-confirm">
                知情,切云端
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setConsentFor(null);
                  if (consentFor === "start") onChannelChange("local");
                }}
              >
                留在本地
              </Button>
            </div>
          </div>
        ) : null}

        <div>
          <Button
            size="sm"
            onClick={handleStart}
            disabled={!imageId || busy || (mode === "ask" && !question.trim())}
            data-testid="vision-start"
          >
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Eye className="size-3.5" />}
            {busy ? "进行中…" : "开始看图"}
          </Button>
          {!imageId ? <span className="ml-2 text-[11px] text-muted-foreground">先导入图片</span> : null}
          {imageId && mode === "ask" && !question.trim() ? (
            <span className="ml-2 text-[11px] text-muted-foreground">自由提问需先填问题</span>
          ) : null}
        </div>

        {/* 进行态(事件流驱动) */}
        {job.phase === "starting" ? (
          <p className="text-xs text-muted-foreground" data-testid="vision-starting">
            正在创建看图任务…
          </p>
        ) : job.phase === "running" ? (
          <div className="flex flex-col gap-1.5 rounded-md border border-border bg-muted/20 px-3 py-2.5" data-testid="vision-running">
            <p className="flex items-center gap-2 text-sm text-foreground">
              <Loader2 className="size-3.5 animate-spin text-primary" />
              {STAGE_TEXT[job.stage ?? ""] ?? "任务执行中"}(job #{job.jobId})
            </p>
            {typeof job.pct === "number" ? (
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${Math.min(100, Math.max(0, job.pct))}%` }} />
              </div>
            ) : null}
          </div>
        ) : job.phase === "error" ? (
          <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm" data-testid="vision-error">
            <p className="flex items-center gap-1.5 font-medium text-destructive">
              <AlertTriangle className="size-4" />
              {job.error.message}
            </p>
            <p className="mt-1 font-mono text-xs text-muted-foreground">
              code={job.error.code} path={job.error.path}
            </p>
            {job.error.code === "image_unreachable" ? (
              <p className="mt-1 text-xs text-muted-foreground">
                启动指引:本地端点按设置 → 看图的 base_url(默认 http://127.0.0.1:8080/v1,mlx-vlm
                <span className="font-mono"> mlx_vlm.server --model &lt;路径&gt;</span>);LM Studio 备选 :1234/v1。
              </p>
            ) : null}
            {job.error.code === "image_no_credentials" ? (
              <p className="mt-1 text-xs text-muted-foreground">到设置 → 看图填入云端 api_key(只入系统钥匙链)。</p>
            ) : null}
          </div>
        ) : job.phase === "done" ? (
          <div className="flex flex-col gap-2" data-testid="vision-result">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="secondary">{job.outcome.channel === "local" ? "本地" : "云端"}</Badge>
              <Badge variant="outline" className="font-mono" title="模型(本地通道 = 模型路径)">
                {job.outcome.model || "(未指名)"}
              </Badge>
              <span className="text-[11px] text-muted-foreground">{(job.outcome.elapsed_ms / 1000).toFixed(1)} s</span>
              {job.outcome.ocr_used ? <Badge variant="ok">已用 OCR 初稿</Badge> : null}
            </div>
            <p
              ref={resultTextRef}
              className="whitespace-pre-wrap break-words rounded-md border border-border bg-muted/10 px-3 py-2.5 text-sm leading-relaxed text-foreground select-text"
            >
              {job.outcome.text}
            </p>
            <div className="flex items-center gap-2">
              <Button size="sm" variant="outline" onClick={() => void handleCopy()} data-testid="vision-copy">
                复制全文
              </Button>
              {copyState === "ok" ? (
                <span role="status" className="text-xs text-ok">
                  已复制
                </span>
              ) : copyState === "manual" ? (
                <span role="status" className="text-xs text-muted-foreground">
                  自动复制失败:已选中全文,请 Cmd+C 手动复制
                </span>
              ) : null}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
