/**
 * 情报流数据装配(本屏私有 api 模块;共享客户端 @/lib/api 只读不动)。
 *
 * 数据面 = sidecar 协议 store.items(SQLiteStore.list_items 直读,新→旧,
 * 见 entry.py `_m_store_items` / store/sqlite.py `list_items`);服务端搜索
 * query(G1,10-03-feed-ux)随游标全程透传 —— 搜索覆盖已加载页之外的
 * 全库数据,协议级而非本地过滤。
 *
 * 翻页游标(v1.1.2 桌面对齐批 C1,与 feed-ux G1 合流形状):复合游标
 * `(before, before_id)` = 上页最旧条目的 `(first_seen, id)` —— 同刻条目
 * 超单页 limit 也能推进直至取尽(旧「since 复用 + 客户端去重 + added==0
 * 判停」的卡死边界已修)。客户端 dedup 与 added==0 防御判停保留为兜底。
 */
import { api } from "@/lib/api";
import type { FeedItem } from "@/lib/api";

/** 单页条数(与卡片瀑布一屏量级匹配) */
export const FEED_PAGE_SIZE = 50;

/** 翻页请求:cursor = 上页最旧条目的 (first_seen, id) 对(首页两键皆 null) */
export interface FeedPageRequest {
  cursor: string | null;
  /** 复合游标第二键(与 cursor 同源:同刻条目翻页不跳不重) */
  cursorId: number | null;
  pageSize?: number;
  /** 品类过滤(null = 不传参 = 全部品类;C8 Outlet context 直通) */
  category?: string | null;
  /** 服务端搜索词(G1:title/content/source 三列 LIKE NOCASE,随游标透传) */
  query?: string | null;
}

export interface FeedPage {
  /** 服务端原始返回(新→旧;边界条目由 appendFeedPage 去重兜底) */
  items: FeedItem[];
  /** 服务端返回数达到 limit → 可能还有更旧条目(判停 = 返回数 < limit) */
  hasMore: boolean;
  /** 下页游标 = 本页最旧条目的 (first_seen, id)(空页为 null) */
  nextCursor: string | null;
  nextCursorId: number | null;
}

export async function fetchFeedPage(request: FeedPageRequest): Promise<FeedPage> {
  const pageSize = request.pageSize ?? FEED_PAGE_SIZE;
  const result = await api.storeItems({
    limit: pageSize,
    ...(request.cursor
      ? { before: request.cursor, ...(request.cursorId !== null ? { before_id: request.cursorId } : {}) }
      : {}),
    ...(request.category ? { category: request.category } : {}),
    ...(request.query ? { query: request.query } : {}),
  });
  const items = result.items;
  const oldest = items.length > 0 ? items[items.length - 1] : null;
  return {
    items,
    hasMore: items.length >= pageSize,
    nextCursor: oldest?.first_seen ?? null,
    nextCursorId: oldest?.id ?? null,
  };
}

// ---------------------------------------------------------------------------
// 导出当前视图(G3,10-03-feed-ux):dialog.save 选路径 → sidecar 直写;
// 打开原文的 URL 门(G2):仅 http(s) 走 plugin-shell open
// ---------------------------------------------------------------------------

/** 打开原文的 URL 门(G2):仅 http(s) 渲染「打开原文」(capabilities 同门) */
export function isOpenableUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
}

/** 导出格式选择(与 feed.export params.format 同词表) */
export type ExportFormat = "jsonl" | "csv";

/** 默认文件名:myia-feed-YYYYMMDD.<ext>(本地日期,与导出按钮同日可见) */
export function defaultExportName(format: ExportFormat, now: Date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  const date = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
  return `myia-feed-${date}.${format}`;
}

/** 保存对话框形状(= @tauri-apps/plugin-dialog save 的参数;注入以便测试) */
export interface SaveDialogFn {
  (opts: {
    defaultPath: string;
    filters: { name: string; extensions: string[] }[];
  }): Promise<string | null>;
}

export interface ExportOutcome {
  /** 用户在系统保存对话框取消 = null(不是错误) */
  path: string | null;
  count: number;
  bytes: number;
}

/**
 * 导出当前过滤视图:``dialog.save()`` 选路径(默认名带日期,覆盖确认归
 * 对话框)→ ``feed.export`` sidecar 直写(数据不经 webview)。
 * 对话框函数注入以便测试;生产缺省 = @tauri-apps/plugin-dialog 的 save。
 */
export async function exportFeedView(
  options: { format: ExportFormat; category?: string | null; query?: string | null },
  saveDialog: SaveDialogFn = defaultSaveDialog,
): Promise<ExportOutcome> {
  const path = await saveDialog({
    defaultPath: defaultExportName(options.format),
    filters:
      options.format === "jsonl"
        ? [{ name: "JSON Lines", extensions: ["jsonl"] }]
        : [{ name: "CSV", extensions: ["csv"] }],
  });
  if (path === null) return { path: null, count: 0, bytes: 0 };
  const result = await api.feedExport({
    format: options.format,
    path,
    ...(options.category ? { category: options.category } : {}),
    ...(options.query ? { query: options.query } : {}),
  });
  return { path: result.path, count: result.count, bytes: result.bytes };
}

/** 生产保存对话框(延迟 import;浏览器直开时 save 不可用属预期错误路径) */
async function defaultSaveDialog(opts: {
  defaultPath: string;
  filters: { name: string; extensions: string[] }[];
}): Promise<string | null> {
  const dialog = await import("@tauri-apps/plugin-dialog");
  return dialog.save(opts);
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
// 展示辅助:精评分数徽标 / 相对时间 / 品类色(D4)/ 时间分组(D4)
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

// ---- D4(10-03-ui-deep-imitation):品类色 + 分组时间轴 ----

/** 品类色板:品牌青/紫领衔的 8 色邻位环(暗面可读、饱和度同档;Linear label 式) */
const CATEGORY_PALETTE = [
  "#22d3ee", // 品牌青
  "#8b5cf6", // 品牌紫
  "#3dd68c", // ok 绿
  "#f5b544", // warning 琥珀
  "#60a5fa", // 蓝
  "#f472b6", // 粉
  "#2dd4bf", // 青绿
  "#fb7185", // 玫红
] as const;

/** djb2 字符串散列(稳定无依赖:同品类恒同色,跨会话/跨端不变) */
function hashString(text: string): number {
  let hash = 5381;
  for (let index = 0; index < text.length; index += 1) {
    hash = ((hash << 5) + hash + text.charCodeAt(index)) >>> 0;
  }
  return hash;
}

/**
 * 品类色:卡片左侧品类色条与品类徽标同源取色;无品类 → null(不渲染色件)。
 * 色值是 6 位 hex,透明度由消费侧拼 8 位 hex(hex+alpha)或 opacity 控制。
 */
export function categoryColor(category: string | null | undefined): string | null {
  if (!category) return null;
  return CATEGORY_PALETTE[hashString(category) % CATEGORY_PALETTE.length];
}

/** 时间分组桶 key(新→旧) */
export type FeedGroupKey = "today" | "yesterday" | "week" | "earlier";

export interface FeedGroup {
  key: FeedGroupKey;
  /** 分组头文案(中文界面) */
  label: string;
  items: FeedItem[];
}

const GROUP_LABELS: Record<FeedGroupKey, string> = {
  today: "今天",
  yesterday: "昨天",
  week: "7 天内",
  earlier: "更早",
};

const DAY_MS = 86_400_000;

/**
 * 分组时间轴(D4):按 first_seen 落 今天 / 昨天 / 7 天内 / 更早 四桶,
 * 保持传入顺序(新→旧),空桶不出组;first_seen 缺失/无效归「更早」。
 * `now` 注入以便测试(边界:今天 0 点、昨天 0 点、7 天窗)。
 */
export function groupFeedItems(items: FeedItem[], now: Date = new Date()): FeedGroup[] {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const buckets: Record<FeedGroupKey, FeedItem[]> = { today: [], yesterday: [], week: [], earlier: [] };
  for (const item of items) {
    const seen = item.first_seen ? new Date(item.first_seen).getTime() : Number.NaN;
    if (Number.isNaN(seen) || seen < startOfToday - 7 * DAY_MS) {
      buckets.earlier.push(item);
    } else if (seen >= startOfToday) {
      buckets.today.push(item);
    } else if (seen >= startOfToday - DAY_MS) {
      buckets.yesterday.push(item);
    } else {
      buckets.week.push(item);
    }
  }
  const order: FeedGroupKey[] = ["today", "yesterday", "week", "earlier"];
  return order
    .filter((key) => buckets[key].length > 0)
    .map((key) => ({ key, label: GROUP_LABELS[key], items: buckets[key] }));
}
