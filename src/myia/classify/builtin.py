"""Built-in seven-category keyword classifier (first funnel: fast, zero token)."""

from __future__ import annotations

SEVEN_CATEGORIES = [
    "credit-card",
    "proxy-node",
    "buying-agent",
    "server",
    "token",
    "ai-news",
    "freebie",
]

# Seed rules only; production keyword sets are tuned via the push feedback loop.
KEYWORD_RULES: dict[str, list[str]] = {
    "credit-card": ["信用卡", "办卡", "返现", "credit card", "cashback"],
    "proxy-node": ["节点", "机场", "订阅", "clash", "v2ray", "proxy"],
    "buying-agent": ["代买", "代购", "代下", "拼单"],
    "server": ["vps", "服务器", "云主机", "独服", "dedicated"],
    "token": ["token", "api key", "apikey", "兑换码", "额度"],
    "ai-news": ["ai", "大模型", "llm", "gpt", "开源模型"],
    "freebie": ["羊毛", "白嫖", "免费", "0元", "优惠", "free"],
}


def classify_item(item) -> str | None:
    """Return the best-matching category for an item, or None.

    Simple keyword scan over title + url (case-insensitive); the category
    with the most keyword hits wins, ties break by SEVEN_CATEGORIES order.
    LLM precision scoring happens later in enrich; this layer only
    pre-filters.
    """
    haystack = f"{getattr(item, 'title', '')} {getattr(item, 'url', '')}".lower()
    best: tuple[int, str] | None = None
    for category in SEVEN_CATEGORIES:
        hits = sum(1 for kw in KEYWORD_RULES.get(category, []) if kw.lower() in haystack)
        if hits and (best is None or hits > best[0]):
            best = (hits, category)
    return best[1] if best else None
