/**
 * 情报流数据装配(本屏私有 api 模块;共享客户端 @/lib/api 只读不动)。
 *
 * 数据面 = sidecar 协议 store.items(SQLiteStore.list_items 直读,新→旧,
 * 见 entry.py `_m_store_items` / store/sqlite.py `list_items`)。
 *
 * 协议缺口注记:store.items 只有 `since`(first_seen 下界,**含边界**)与
 * `limit`,没有 before/offset —— 深翻页采用「游标 = 上页最旧条目的 first_seen
 * + 客户端按 dedup_key 去重」。边界条目会重复返回由去重消化;若同刻
 * (同 first_seen)条目数超过单页 limit,游标无法推进,追加 0 条即判停。
 * 协议补 before 前,此处如实截断,不伪造加载成功。
 */
import { api } from "@/lib/api";
import type { FeedItem } from "@/lib/api";

/** 单页条数(与卡片瀑布一屏量级匹配) */
export const FEED_PAGE_SIZE = 50;

/** 翻页请求:cursor = 上一页最旧条目的 first_seen(首页 null) */
export interface FeedPageRequest {
  cursor: string | null;
  pageSize?: number;
  category?: string;
}

export interface FeedPage {
  /** 服务端原始返回(新→旧;含上页边界条目,由 appendFeedPage 去重) */
  items: FeedItem[];
  /** 服务端返回数达到 limit → 可能还有更旧条目 */
  hasMore: boolean;
  /** 下页游标 = 本页最旧条目的 first_seen(空页为 null) */
  nextCursor: string | null;
}

export async function fetchFeedPage(request: FeedPageRequest): Promise<FeedPage> {
  const pageSize = request.pageSize ?? FEED_PAGE_SIZE;
  const result = await api.storeItems({
    limit: pageSize,
    ...(request.cursor ? { since: request.cursor } : {}),
    ...(request.category ? { category: request.category } : {}),
  });
  const items = result.items;
  return {
    items,
    hasMore: items.length >= pageSize,
    nextCursor: items.length > 0 ? (items[items.length - 1].first_seen ?? null) : null,
  };
}

/** 条目稳定 key:dedup_key 优先,id 兜底(防御 null id → url) */
export function itemKey(item: FeedItem): string {
  if (item.dedup_key) return item.dedup_key;
  return `id:${item.id ?? item.url}`;
}

/** 追加一页:滤除已加载条目后拼接(保持新→旧);返回新增数供判停 */
export function appendFeedPage(loaded: FeedItem[], page: FeedPage): { items: FeedItem[]; added: number } {
  const seen = new Set(loaded.map(itemKey));
  const fresh = page.items.filter((item) => !seen.has(itemKey(item)));
  return { items: [...loaded, ...fresh], added: fresh.length };
}

// ---------------------------------------------------------------------------
// 未读 / 星标 / 稍后读三态(本地态:localStorage 持久,键 = itemKey)
// ---------------------------------------------------------------------------

export interface FeedItemState {
  read?: boolean;
  starred?: boolean;
  later?: boolean;
}

/** key(itemKey)→ 三态;未出现的 key 视为 全 false */
export type FeedStateMap = Record<string, FeedItemState>;

const STORAGE_KEY = "myia.feed.states.v1";

export function loadFeedStates(storage: Storage | null = typeof window === "undefined" ? null : window.localStorage): FeedStateMap {
  if (storage === null) return {};
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return parsed as FeedStateMap;
  } catch {
    // 损坏即弃用本地态(不阻断情报流);下次切换标记时会重写
    return {};
  }
}

export function saveFeedStates(
  states: FeedStateMap,
  storage: Storage | null = typeof window === "undefined" ? null : window.localStorage,
): void {
  if (storage === null) return;
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(states));
  } catch {
    // 配额/隐私模式写失败不阻断界面(本地态尽力而为)
  }
}

/** 纯切换:read(未读⇄已读)/ starred / later;返回新 map 不改原对象 */
export function toggleMarker(states: FeedStateMap, key: string, marker: keyof FeedItemState): FeedStateMap {
  const current = states[key] ?? {};
  const next: FeedItemState = { ...current, [marker]: !current[marker] };
  const empty = !next.read && !next.starred && !next.later;
  const result: FeedStateMap = { ...states };
  if (empty) {
    delete result[key];
  } else {
    result[key] = next;
  }
  return result;
}

export type FeedFilter = "unread" | "starred" | "later" | "all";

/** 过滤视图:未读 = 未标记已读;星标/稍后读按各自标记;全部 = 不过滤 */
export function applyFeedFilter(items: FeedItem[], states: FeedStateMap, filter: FeedFilter): FeedItem[] {
  if (filter === "all") return items;
  return items.filter((item) => {
    const state = states[itemKey(item)] ?? {};
    if (filter === "unread") return !state.read;
    if (filter === "starred") return state.starred === true;
    return state.later === true;
  });
}

// ---------------------------------------------------------------------------
// 展示辅助:精评分数徽标 / 相对时间
// ---------------------------------------------------------------------------

/**
 * 主分数:enrich scores 形如 {维度: 分值}(schema enrich.scores 命名维度),
 * 取最大数值;无 scores 或全非数值 → null(不显示徽标)。
 */
export function primaryScore(item: FeedItem): number | null {
  if (item.scores === null || item.scores === undefined) return null;
  const values = Object.values(item.scores).filter((value): value is number => typeof value === "number");
  if (values.length === 0) return null;
  return Math.max(...values);
}

/** 相对时间:刚刚 / N 分钟前 / N 小时前 / N 天前;超过 7 天落 YYYY-MM-DD HH:mm */
export function formatRelativeTime(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "—";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return iso;
  const diffMs = now.getTime() - then.getTime();
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} 天前`;
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    `${then.getFullYear()}-${pad(then.getMonth() + 1)}-${pad(then.getDate())} ` +
    `${pad(then.getHours())}:${pad(then.getMinutes())}`
  );
}
