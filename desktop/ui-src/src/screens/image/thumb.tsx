import { FileImage, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/** 已入库图片(screen 级状态:导入应答 + 展示元信息;previewUrl 仅 base64 入口有) */
export interface ImportedImage {
  id: string;
  path: string;
  bytes: number;
  ext: string;
  name: string;
  /** 本地预览(data: URL,CSP 已放行);路径入口(选择器/拖放)无内容,为 null */
  previewUrl: string | null;
}

interface ThumbProps {
  image: ImportedImage;
  /** 重选 = 清空并回到 DropZone */
  onClear: () => void;
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

/**
 * 缩略图 + 元信息 + 重新选/清空(v1 单图:导入新图即替换)。
 * 路径入口(系统选择器/Tauri 拖放)webview 拿不到文件内容(无 fs 权限,
 * 这是最小权限的代价):显示占位图标 + 文件名/大小,OCR 与二级看图不受影响。
 */
export function Thumb({ image, onClear }: ThumbProps) {
  return (
    <div data-testid="image-thumb" className="flex items-center gap-3 rounded-md border border-border bg-card p-3">
      <div className="flex size-20 shrink-0 items-center justify-center overflow-hidden rounded-md border border-border bg-muted/30">
        {image.previewUrl ? (
          <img src={image.previewUrl} alt={image.name} className="size-full object-contain" />
        ) : (
          <FileImage className="size-8 text-muted-foreground" aria-label="路径导入无预览" />
        )}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="truncate text-sm font-medium text-foreground" title={image.name}>
          {image.name}
        </p>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="outline">{image.ext}</Badge>
          <span className="text-xs text-muted-foreground">{formatBytes(image.bytes)}</span>
          <span className="font-mono text-[11px] text-muted-foreground" title={image.path}>
            id={image.id}
          </span>
        </div>
        {!image.previewUrl ? (
          <p className="text-[11px] text-muted-foreground">路径导入:缩略图不可见(最小权限,无 fs 读);识别不受影响</p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button variant="outline" size="sm" onClick={onClear} aria-label="重新选图(清空当前)">
          <RefreshCw className="size-3.5" />
          重选 / 清空
        </Button>
      </div>
    </div>
  );
}
