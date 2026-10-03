# AC1 零影响对比(10-03-detail-images):HEAD worktree(关前,61a2e69,无 detail 环)
# vs 当前工作树(关后,uncommitted detail 实现)——同品类 e2e-nodefault(images 节
# 开启、**不写 detail 键**,缺省 detail_fetch=false),各跑一次 shishi run --json
# (独立 db),归一化后逐字段对比。
import json
import sqlite3

VOLATILE = {"run_id", "started_at", "finished_at", "resumed_from_run_id", "maintenance", "duration_seconds"}

def norm(obj):
    if isinstance(obj, dict):
        return {k: norm(v) for k, v in sorted(obj.items()) if k not in VOLATILE}
    if isinstance(obj, list):
        return [norm(v) for v in obj]
    return obj

head = json.load(open("/tmp/myia-di-e2e/runs/head-nodefault/head-nodefault-run.json"))
cur = json.load(open("/tmp/myia-di-e2e/runs/nodefault/nodefault-run.json"))
h, c = norm(head), norm(cur)
print("run 结果 JSON(剥时序字段)对比:", "IDENTICAL" if h == c else "DIFF")
if h != c:
    for k in h:
        if h[k] != c.get(k):
            print("  DIFF key:", k, "\n    head:", json.dumps(h[k], ensure_ascii=False)[:300], "\n    cur :", json.dumps(c.get(k), ensure_ascii=False)[:300])

def rows(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute(
        "SELECT url, dedup_key, source, title, content, content_hash, tags, category, scores, raw FROM items ORDER BY url")]
    con.close()
    return out

rh = rows("/tmp/myia-di-e2e/runs/head-nodefault/myia.db")
rc = rows("/tmp/myia-di-e2e/runs/nodefault/myia.db")
print(f"items 表逐字段({len(rh)} vs {len(rc)} 行):", "IDENTICAL" if rh == rc else "DIFF")
if rh != rc:
    for a, b in zip(rh, rc):
        if a != b:
            print("  DIFF row:", a["url"])
            for k in a:
                if a[k] != b.get(k):
                    print("    ", k, "head:", str(a[k])[:120], "cur:", str(b.get(k))[:120])

def digest(path):
    for line in open(path):
        line = line.strip()
        if line.startswith('{"channel"'):
            d = json.loads(line)
            return sorted((i.get("url"), i.get("title")) for i in d["items"])
    return None

dh = digest("/tmp/myia-di-e2e/runs/head-nodefault/head-nodefault-run.stderr.log")
dc = digest("/tmp/myia-di-e2e/runs/nodefault/nodefault-run.stderr.log")
print("stdout 推送 digest 条目集:", "IDENTICAL" if dh == dc else "DIFF")
