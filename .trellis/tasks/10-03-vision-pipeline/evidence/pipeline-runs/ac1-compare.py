# AC1 零影响对比:HEAD(关前)vs 当前树(关后)——同品类 e2e-noimg(无 images 节)
# 方法:git worktree @ /tmp/myia-e2e/baseline-wt(HEAD,无 vision/collect.py)+
#       当前工作树,各跑一次 myia run --json(独立 db),归一化后逐字段对比。
# 运行时间:2026-10-03 14:2x;HEAD=62cc4a8(并行会话推进后,vision-pipeline 仍未提交)。
import json
import sqlite3

VOLATILE = {"run_id", "started_at", "finished_at", "resumed_from_run_id", "maintenance", "duration_seconds"}

def norm(obj):
    if isinstance(obj, dict):
        return {k: norm(v) for k, v in sorted(obj.items()) if k not in VOLATILE}
    if isinstance(obj, list):
        return [norm(v) for v in obj]
    return obj

head = json.load(open("/tmp/myia-e2e/baseline-head.json"))
cur = json.load(open("/tmp/myia-e2e/baseline-current.json"))
h, c = norm(head), norm(cur)
print("run 结果 JSON(剥时序字段)对比:", "IDENTICAL" if h == c else "DIFF")
if h != c:
    for k in h:
        if h[k] != c.get(k):
            print("  DIFF key:", k)

def rows(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute(
        "SELECT url, dedup_key, source, title, content, content_hash, tags, category, scores, raw FROM items ORDER BY url")]
    con.close()
    return out

rh = rows("/tmp/myia-e2e/runs/baseline/head/myia.db")
rc = rows("/tmp/myia-e2e/runs/baseline/current/myia.db")
print(f"items 表逐字段({len(rh)} vs {len(rc)} 行):", "IDENTICAL" if rh == rc else "DIFF")

def digest(path):
    for line in open(path):
        line = line.strip()
        if line.startswith('{"channel"'):
            d = json.loads(line)
            return sorted((i.get("url"), i.get("title"), i.get("image_status"), i.get("image_ocr")) for i in d["items"])
    return None

dh = digest("/tmp/myia-e2e/baseline-head.stderr.log")
dc = digest("/tmp/myia-e2e/baseline-current.stderr.log")
print("stdout 推送 digest 条目集:", "IDENTICAL" if dh == dc else "DIFF")
