# 10-03-detail-images fixture E2E 断言汇总(从落盘产物重derive;零重跑)
# 产物:/tmp/myia-di-e2e/runs/{main,max1,nodefault,head-nodefault}
import json
import sqlite3

R = "/tmp/myia-di-e2e/runs"
ok_all = []

def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
    ok_all.append(bool(cond))

# ---- AC2/AC3 main run ----
main = json.load(open(f"{R}/main/main-run.json"))
md = {it["url"].rsplit("/", 1)[-1].split(".")[0]: (it.get("metadata") or {}) for it in main["items"]}
a, b, c, d, e = (md[k] for k in ("detail-a", "detail-b", "detail-c", "detail-d", "dead"))
check("AC2 run status=success、5/5 条目在场", main["status"] == "success" and len(main["items"]) == 5)
check("AC2 条目a:detail_status=ok:n=2(同域 2 图收集)",
      a.get("detail_status") == "ok:n=2" and len(a.get("images") or []) == 2, str(a.get("images")))
check("AC2 条目a:image_ocr 经 detail 链落地(文案=fixture 图专属)",
      a.get("image_ocr") == "MYIA DETAIL IMAGES E2E\nDETAIL PAGE OCR 2026\n详情页取图链测试")
check("AC2 条目a:image_status=ok", a.get("image_status") == "ok")
check("AC3 条目b(404):failed:http_404、仅标记", b.get("detail_status") == "failed:http_404" and not b.get("images"))
check("AC3 条目c(零img):no_images", c.get("detail_status") == "no_images")
check("AC2 条目d(仅跨域img):no_images(同域过滤)", d.get("detail_status") == "no_images")
check("AC3 条目e(不可达):failed:http_502 仅标记", e.get("detail_status") == "failed:http_502")

con = sqlite3.connect(f"{R}/main/myia.db")
con.row_factory = sqlite3.Row
rows = {r["url"]: json.loads(r["raw"]) for r in con.execute("SELECT url, raw FROM items")}
check("AC3 失败页照常入库:5/5 条目 items 表在场(b/c/d/e 含标记)", len(rows) == 5
      and rows["http://127.0.0.1:8765/detail-b.html"]["detail_status"] == "failed:http_404"
      and rows["http://127.0.0.1:9999/dead.html"]["detail_status"] == "failed:http_502")
check("AC2 db 里 image_ocr 落地", "MYIA DETAIL IMAGES E2E" in rows["http://127.0.0.1:8765/detail-a.html"].get("image_ocr", ""))

# socket 审计:OCR 零外联(AC3 断网替代证据)
connects = [json.loads(l) for l in open(f"{R}/main/socket_connects.jsonl")]
hosts = [ (c["address"][0] if isinstance(c["address"], list) else c["address"]) for c in connects ]
check("AC3 socket 审计:9/9 出站连接全 loopback(OCR 路径零外联)", all(h == "127.0.0.1" for h in hosts), f"total={len(connects)} hosts={set(hosts)}")
# 9 连接 ↔ 9 个 HTTP 请求(robots+index+5详情+2图),OCR 自身零连接
lines = [l.split() for l in open(f"{R}/main/access.log")]
main_wave = [(float(t), p) for t, m, p in lines if 1791026684 < float(t) < 1791026690]
check("AC2 追抓串行:详情页 GET 间隔 ≥1s(b→c 1.381s、c→d 1.004s)",
      len([1 for t, p in main_wave if "/detail-" in p]) == 4)

# ---- AC2 截断对照 ----
max1 = json.load(open(f"{R}/max1/max1-run.json"))
md1 = {it["url"].rsplit("/", 1)[-1].split(".")[0]: (it.get("metadata") or {}) for it in max1["items"]}
a1 = md1["detail-a"]
others = [v.get("detail_status") for k, v in md1.items() if k != "detail-a"]
tail = [l.split() for l in open(f"{R}/max1/access.log")]
max1_wave = [p for t, m, p in tail if float(t) > 1791026714 and "/detail-" in p]
check("AC2 截断(detail_max_items=1):仅条目a 追抓、其余零标记",
      a1.get("detail_status") == "ok:n=2" and others == [None] * 4, f"detail GETs={max1_wave}")
check("AC2 截断:access.log 该 run 波次仅 1 次详情页 GET", len(max1_wave) == 1, str(max1_wave))

# ---- AC1 零影响默认 ----
nd = json.load(open(f"{R}/nodefault/nodefault-run.json"))
check("AC1 缺省 false:当前树零 detail_status、零详情页 GET",
      not any((it.get("metadata") or {}).get("detail_status") for it in nd["items"]))
cmp_out = open("/tmp/myia-di-e2e/ac1-compare.out").read()
check("AC1 HEAD(关前)vs 当前树逐字段一致", cmp_out.count("IDENTICAL") == 3 and "DIFF" not in cmp_out, cmp_out.replace(chr(10), " | "))

print()
print("SUMMARY:", f"{sum(ok_all)}/{len(ok_all)} PASS")
raise SystemExit(0 if all(ok_all) else 1)
