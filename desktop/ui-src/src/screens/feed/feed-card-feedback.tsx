import { useCallback, useState } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, SidecarRequestError } from "@/lib/api";
import type { FeedItem } from "@/lib/api";

/**
 * 情报流卡片反馈:👍/👎 → feedback.mark(channel=desktop;B2,10-03-v112-desktop-parity)。
 *
 * 身份传 dedup_key(resolve_item_ref 同门:条目行还在时按 key 命中并快照
 * title/category);已标即置灰 —— record_feedback 的幂等键是
 * (channel, external_id),手动标记无 external_id,重复点击会重复入库,
 * 置灰是 UI 侧防重(不虚构服务端幂等)。标记态只驻卡片本地(每次进屏
 * 重置;历史标记经 CLI/仪表盘反馈统计可见,不在卡片回放)。
 */
export function FeedCardFeedback({ item }: { item: FeedItem }) {
  const [marked, setMarked] = useState<"good" | "bad" | null>(null);
  const [pending, setPending] = useState<"good" | "bad" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mark = useCallback(
    async (verdict: "good" | "bad") => {
      setPending(verdict);
      setError(null);
      try {
        await api.feedbackMark({ item: item.dedup_key, verdict });
        setMarked(verdict);
      } catch (err) {
        setError(err instanceof SidecarRequestError ? `${err.code}: ${err.message}` : String(err));
      } finally {
        setPending(null);
      }
    },
    [item.dedup_key],
  );

  const disabled = marked !== null || pending !== null;
  return (
    <span className="flex items-center gap-0.5" data-testid={`feed-feedback-${item.id ?? item.dedup_key}`}>
      <Button
        variant="ghost"
        size="icon"
        className="size-6"
        aria-label="好评"
        aria-pressed={marked === "good"}
        title={marked === "good" ? "已好评(反馈入库,channel=desktop)" : "好评:入库参与评分调参(feedback.mark)"}
        disabled={disabled}
        onClick={() => void mark("good")}
      >
        <ThumbsUp
          className={
            marked === "good" ? "size-3.5 fill-ok text-ok" : "size-3.5 text-muted-foreground"
          }
        />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="size-6"
        aria-label="差评"
        aria-pressed={marked === "bad"}
        title={
          marked === "bad"
            ? "已差评(负反馈将参与调参:Top 类目/词降权)"
            : "差评:负反馈参与下轮调参(feedback.mark)"
        }
        disabled={disabled}
        onClick={() => void mark("bad")}
      >
        <ThumbsDown
          className={
            marked === "bad"
              ? "size-3.5 fill-destructive text-destructive"
              : "size-3.5 text-muted-foreground"
          }
        />
      </Button>
      {error ? (
        <span className="max-w-40 truncate text-[11px] text-destructive" title={error}>
          反馈失败
        </span>
      ) : null}
    </span>
  );
}
