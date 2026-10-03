from datetime import datetime
from zoneinfo import ZoneInfo
from apscheduler.triggers.cron import CronTrigger

NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
NAME_TO_NUM = {n: i for i, n in enumerate(NAMES)}

def _tok(t: str) -> int:
    t = t.strip().lower()
    if t in NAME_TO_NUM:
        return NAME_TO_NUM[t]
    return int(t)

def normalize_dow(field: str) -> str:
    days: set[int] = set()
    for item in field.split(","):
        item = item.strip().lower()
        if not item:
            raise ValueError(f"empty dow item in {field!r}")
        base, _, step_s = item.partition("/")
        step = int(step_s) if step_s else 1
        if step <= 0:
            raise ValueError(f"bad step in {field!r}")
        if base == "*":
            lo, hi = 0, 6
        elif "-" in base:
            lo_s, _, hi_s = base.partition("-")
            lo, hi = _tok(lo_s), _tok(hi_s)
        else:
            lo = _tok(base)
            hi = 6 if step_s else lo
        if not (0 <= lo <= 7 and 0 <= hi <= 7):
            raise ValueError(f"dow out of range in {field!r}")
        if lo <= hi:
            seq = list(range(lo, hi + 1, step))
        else:  # wraparound (e.g. 5-1 = fri..mon via 7)
            seq = list(range(lo, 8, step)) + list(range(0, hi + 1, step))
        for d in seq:
            days.add(d % 7)
    return ",".join(NAMES[d] for d in sorted(days))

tz = ZoneInfo("Asia/Shanghai")
base_mon = datetime(2026, 10, 5, 12, 0, tzinfo=tz)

cases = {
    "0": "sun", "1": "mon", "7": "sun",
    "*/2": "sun,tue,thu,sat",
    "1-5": "mon,tue,wed,thu,fri",
    "0,6": "sun,sat",
    "5-1": "sun,mon,fri,sat",
    "mon": "mon",
    "mon-fri": "mon,tue,wed,thu,fri",
    "sun,mon": "sun,mon",
}
for field, want in cases.items():
    got = normalize_dow(field)
    print("OK " if got == want else "DIFF", repr(field), "->", got)
    assert got == want

norm = normalize_dow("*/2")
t = CronTrigger.from_crontab(f"0 9 * * {norm}", timezone=tz)
nf = t.get_next_fire_time(None, base_mon)
print("cron */2 from Mon noon ->", nf.strftime("%a"), "(cron-correct: Tue)")
assert nf.strftime("%a") == "Tue"
print("ALL OK")
