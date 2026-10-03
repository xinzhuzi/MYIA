"""Probe round 2: extract_html on IGN RSS 2.0 + Reddit Atom (link@href form)."""
import sys
from pathlib import Path

EV = Path(__file__).resolve().parent  # 本 evidence 目录(路径随仓走,勿写本机绝对路径)
sys.path.insert(0, str(EV.parents[3] / "src"))  # evidence→games-wrap→tasks→.trellis→仓根/src
from myia.engines.fetch_base import extract_html
from myia.schema import ExtractConfig

ign = (EV / "news-probe-ign-rss.xml").read_text(encoding="utf-8")
atom = (EV / "news-probe-reddit-atom.xml").read_text(encoding="utf-8")

def run(label, doc, base, **cfg):
    try:
        items = extract_html(doc, ExtractConfig(**cfg), base_url=base)
        keys = sorted({k for it in items for k in it})
        print(f"--- {label}: {len(items)} items, union of keys = {keys}")
        for it in items[:2]:
            print("   ", {k: str(v)[:70] for k, v in it.items()})
    except Exception as exc:
        print(f"--- {label}: RAISED {type(exc).__name__}: {exc}")

# IGN RSS 2.0 — can we get url from <link>text</link>?
run("IGN rss: item, url=link (text)", ign, "https://www.ign.com/rss/articles/feed",
    type="list", item="item", fields={"title": "title", "url": "link"})
run("IGN rss: item, url=link@href", ign, "https://www.ign.com/rss/articles/feed",
    type="list", item="item", fields={"title": "title", "url": "link@href"})
run("IGN rss: pubDate/description", ign, "https://www.ign.com/rss/articles/feed",
    type="list", item="item", fields={"title": "title", "url": "link", "pubDate": "pubDate", "desc": "description"})

# Reddit Atom — link href attribute form (void element WITH attrs)
run("Reddit atom: entry, url=link@href", atom, "https://www.reddit.com/r/GamingNews/.rss",
    type="list", item="entry", fields={"title": "title", "url": "link@href"})
run("Reddit atom: entry, url=link text", atom, "https://www.reddit.com/r/GamingNews/.rss",
    type="list", item="entry", fields={"title": "title", "url": "link"})
