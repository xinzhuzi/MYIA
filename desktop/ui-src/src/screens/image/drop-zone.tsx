import { FolderOpen, ImagePlus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { getCurrentWebview } from "@tauri-apps/api/webview";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { asSidecarError, selectImageViaDialog } from "./api";

interface DropZoneProps {
  /** 导入进行中(禁用再选) */
  disabled?: boolean;
  /** 路径入口(系统选择器/Tauri 拖放)→ image.import kind=path */
  onPath: (path: string) => void;
  /** 文件入口(HTML5 drop/粘贴)→ 读 base64 → image.import kind=base64 */
  onFile: (file: File) => void;
  /** 选择器/订阅级错误(结构化;导入错误由父级统一展示) */
  onError: (error: ReturnType<typeof asSidecarError>) => void;
}

/**
 * 图片进入区(四条路,v1 单图;拖/贴/HTML5 drop 零权限,选择器走 dialog 插件):
 *
 * 1. HTML5 drop —— webview 转发拖放时给 File(读 base64,可出缩略图);
 * 2. Tauri webview 拖放事件 —— dragDropEnabled 默认开,macOS/Windows 上
 *    Tauri 拦截拖放时 HTML5 drop 不达,此路给路径(kind=path);
 * 3. 粘贴 —— clipboardData.files(截图直贴的主路径);
 * 4. 「选择图片」—— tauri-plugin-dialog 系统选择器。
 *
 * 同一次物理拖放在个别平台两条路都触发:按内容标识(文件名+大小 / 路径)
 * 1s 内去重,且 sidecar image.import 按 sha256 去重,双保险。
 */
export function DropZone({ disabled = false, onPath, onFile, onError }: DropZoneProps) {
  const [dragActive, setDragActive] = useState(false);
  /** 双路去重:同一次物理拖放在个别平台 Tauri 事件与 HTML5 drop 都触发 ——
   *  同一内容标识(文件名+大小 / 路径)1s 内只接受一次;不同文件不受影响 */
  const lastAcceptedAt = useRef<Map<string, number>>(new Map());
  /** 回调经 ref 转发:Tauri 订阅只建立一次,回调更新不重订阅 */
  const onPathRef = useRef(onPath);
  onPathRef.current = onPath;
  const disabledRef = useRef(disabled);
  disabledRef.current = disabled;

  const acceptDrop = useCallback((key: string, accept: () => void) => {
    if (disabledRef.current) return;
    const now = Date.now();
    const previous = lastAcceptedAt.current.get(key) ?? 0;
    if (now - previous < 1000) return; // 双路重复触发,同内容按抖动丢弃
    lastAcceptedAt.current.set(key, now);
    accept();
  }, []);

  // Tauri webview 拖放(路径路)。订阅失败(非 Tauri 环境/权限缺失)不致命:
  // HTML5 drop 与粘贴仍可用。
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let cancelled = false;
    getCurrentWebview()
      .onDragDropEvent((event) => {
        if (event.payload.type !== "drop") return;
        const path = event.payload.paths[0];
        if (!path) return;
        acceptDrop(path, () => onPathRef.current(path));
      })
      .then((un) => {
        if (cancelled) un();
        else unlisten = un;
      })
      .catch(() => {
        // 拖放事件订阅不可用:静默降级(HTML5/粘贴/选择器仍在)
      });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [acceptDrop]);

  // 粘贴:窗口级监听(截图 Cmd+Ctrl+Shift+4 → Cmd+V 直贴)
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      const file = Array.from(event.clipboardData?.files ?? [])[0];
      if (!file) return;
      event.preventDefault();
      acceptDrop(`${file.name}:${file.size}`, () => onFile(file));
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [acceptDrop, onFile]);

  const handleSelect = useCallback(async () => {
    try {
      const path = await selectImageViaDialog();
      if (path) onPath(path);
    } catch (raw) {
      onError(asSidecarError(raw));
    }
  }, [onError, onPath]);

  return (
    <div
      data-testid="image-dropzone"
      role="button"
      tabIndex={0}
      aria-label="拖入、粘贴或选择图片"
      aria-disabled={disabled}
      className={cn(
        "flex min-h-44 cursor-pointer flex-col items-center justify-center gap-2 rounded-md border border-dashed px-6 py-8 text-center transition-colors",
        dragActive ? "border-primary bg-primary/5" : "border-border hover:border-primary/50 hover:bg-muted/30",
        disabled && "cursor-not-allowed opacity-60",
      )}
      onClick={() => void handleSelect()}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          void handleSelect();
        }
      }}
      onDragOver={(event) => {
        event.preventDefault();
        setDragActive(true);
      }}
      onDragLeave={() => setDragActive(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragActive(false);
        const file = event.dataTransfer.files[0];
        if (!file) return;
        acceptDrop(`${file.name}:${file.size}`, () => onFile(file));
      }}
    >
      <ImagePlus className={cn("size-8", dragActive ? "text-primary" : "text-muted-foreground")} />
      <div className="text-sm text-foreground">
        {dragActive ? "松手即导入" : "拖入 / 粘贴(Cmd+V)截图,或点击选择"}
      </div>
      <div className="text-xs text-muted-foreground">png / jpg / webp / heic,单图 ≤ 10MB;heic 自动转 png 后入库</div>
      <Button
        variant="outline"
        size="sm"
        className="mt-1 pointer-events-none"
        tabIndex={-1}
        aria-hidden
        disabled={disabled}
      >
        <FolderOpen className="size-3.5" />
        选择图片…
      </Button>
    </div>
  );
}
