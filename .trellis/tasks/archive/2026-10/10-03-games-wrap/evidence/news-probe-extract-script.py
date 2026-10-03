"""Probe: can MYIA static_html's extract_html (selectolax) parse RSS XML?

Runs MYIA's REAL extraction path (myia.engines.fetch_base.extract_html +
ExtractConfig) against the live-fetched gcores RSS XML (saved 2026-10-03).
"""
import sys
from pathlib import Path

EV = Path(__file__).resolve().parent  # 本 evidence 目录(路径随仓走,勿写本机绝对路径)
sys.path.insert(0, str(EV.parents[3] / "src"))  # evidence→games-wrap→tasks→.trellis→仓根/src

from myia.engines.fetch_base import extract_html
from myia.schema import ExtractConfig

rss = (EV / "news-probe-gcores-rss.xml").read_text(encoding="utf-8")

cases = [
    ("A: item + title text / url=link text (naive RSS-as-HTML)", dict(
        type="list", item="item",
        fields={"title": "title", "url": "link"},
    )),
    ("B: url=link@href attribute (Atom-style)", dict(
        type="list", item="item",
        fields={"title": "title", "url": "link@href"},
    )),
    ("C: channel > item + pubDate/description", dict(
        type="list", item="channel > item",
        fields={"title": "title", "url": "link", "pubDate": "pubDate", "desc": "description"},
    )),
]
for label, cfg in cases:
    try:
        items = extract_html(rss, ExtractConfig(**cfg), base_url="https://www.gcores.com/rss")
        print(f"--- {label}: {len(items)} items")
        for it in items[:3]:
            print("   ", {k: (str(v)[:60] + "…" if len(str(v)) > 60 else v) for k, v in it.items()})
    except Exception as exc:
        print(f"--- {label}: RAISED {type(exc).__name__}: {exc}")
