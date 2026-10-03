"""直接引擎级真跑(10-04-crawl4ai-l3 AC5):不经 pytest,registry.fetch_source
以显式 ``engine: crawl4ai`` 源视图真抓 aihot 文章页,记录条目数 / markdown
长度 / 同域图收集数(shishi.vision.collect.markdown_image_urls)——看图线
JS 页路径联动证据。真实网络 + 真实浏览器;退出码 0 = 出条。

用法:uv run --no-sync python .trellis/tasks/10-04-crawl4ai-l3/evidence/smoke/direct_engine_smoke.py [url]
(默认 aihot 文章页 = tests/test_crawl4ai.py SMOKE_TARGET_DEFAULT;可用
MYIA_SMOKE_TARGET 覆写。)
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

import httpx

from shishi.engines import registry
from shishi.engines.fetch_base import FetchContext
from shishi.schema import SourceConfig
from shishi.store import SQLiteStore

SMOKE_TARGET_DEFAULT = "https://aihot.news/items/bzodztryi4kvwm4kz9mrwb6nn"


async def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else (
        os.environ.get("MYIA_SMOKE_TARGET") or SMOKE_TARGET_DEFAULT
    )
    source = SourceConfig.model_validate({"name": "smoke-direct", "url": target, "engine": "crawl4ai"})
    client = httpx.AsyncClient()
    with tempfile.TemporaryDirectory() as tmp:
        store = SQLiteStore(Path(tmp) / "smoke-store.db")
        context = FetchContext(client=client, store=store)
        started = time.perf_counter()
        outcome = await registry.fetch_source(source, context)
        elapsed = time.perf_counter() - started
        hint_after = store.get_engine_hint(target)
    try:
        await client.aclose()
    except Exception:  # noqa: BLE001 - 关闭失败不影响结果判定
        pass

    items = outcome.items
    content = (items[0].get("content") or "") if items else ""
    try:
        from shishi.vision.collect import markdown_image_urls
    except ImportError:
        images = None
    else:
        images = markdown_image_urls(content, target) if content else []

    print(f"[direct-smoke] target={target}")
    print(f"[direct-smoke] engine={outcome.engine} items={len(items)} "
          f"skipped={outcome.skipped} failures={[f.error_type for f in outcome.failures]}")
    print(f"[direct-smoke] markdown_len={len(content)} title={items[0].get('title', '')!r}" if items
          else "[direct-smoke] markdown_len=0 (no items)")
    print(f"[direct-smoke] same_domain_images={len(images) if images is not None else 'vision-unavailable'}")
    for url in (images or [])[:10]:
        print(f"[direct-smoke]   img {url}")
    print(f"[direct-smoke] hint_after={hint_after}")
    print(f"[direct-smoke] elapsed={elapsed:.2f}s")

    ok = bool(items) and content.strip() and outcome.engine == "crawl4ai"
    print(f"[direct-smoke] RESULT={'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
