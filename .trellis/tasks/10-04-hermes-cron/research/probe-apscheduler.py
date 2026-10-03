from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import apscheduler
from apscheduler.triggers.cron import CronTrigger

print("APScheduler", apscheduler.__version__)
tz = ZoneInfo("Asia/Shanghai")
# 2026-10-05 is a Monday (today 2026-10-04 is Sunday)
base = datetime(2026, 10, 4, 0, 0, tzinfo=tz)  # Sunday 00:00
print("base:", base, base.strftime("%a"))
for dow in ("0", "1", "2", "sun", "mon", "tue"):
    t = CronTrigger.from_crontab(f"0 9 * * {dow}", timezone=tz)
    nf = t.get_next_fire_time(None, base)
    print(f"dow={dow!r:6} -> {nf} ({nf.strftime('%a') if nf else None})")

# named month/weekday letters
try:
    t = CronTrigger.from_crontab("0 9 * JAN MON", timezone=tz)
    print("names ok:", t.get_next_fire_time(None, base))
except ValueError as e:
    print("names rejected:", e)

# 6-field crontab? (Hermes allows 5-6)
try:
    t = CronTrigger.from_crontab("0 9 * * * *", timezone=tz)
    print("6-field ok")
except ValueError as e:
    print("6-field rejected:", e)

# DST fall-back: America/New_York 2026-11-01, 1:30 occurs twice
ny = ZoneInfo("America/New_York")
t = CronTrigger.from_crontab("30 1 * * *", timezone=ny)
# base = second occurrence of 1:45 (fold=1)
b1 = datetime(2026, 11, 1, 1, 45, tzinfo=ny, fold=0)
b2 = datetime(2026, 11, 1, 1, 45, tzinfo=ny, fold=1)
print("fold0 utc:", b1.astimezone(ZoneInfo("UTC")), "fold1 utc:", b2.astimezone(ZoneInfo("UTC")))
n1 = t.get_next_fire_time(None, b1)
n2 = t.get_next_fire_time(None, b2)
print("next from fold0:", n1, n1.astimezone(ZoneInfo("UTC")))
print("next from fold1:", n2, n2.astimezone(ZoneInfo("UTC")))

# re-anchor semantics: previous_fire_time
t2 = CronTrigger.from_crontab("0 9 * * *", timezone=tz)
prev = datetime(2026, 10, 5, 9, 0, tzinfo=tz)
now = datetime(2026, 10, 4, 0, 0, tzinfo=tz)
print("anchor(prev=mon9, now=sun):", t2.get_next_fire_time(prev, now))
now2 = datetime(2026, 10, 6, 12, 0, tzinfo=tz)  # after prev
print("anchor(prev=mon9, now=tue12):", t2.get_next_fire_time(prev, now2))

# strictly-after guard: base exactly at 9:00 Monday
b3 = datetime(2026, 10, 5, 9, 0, tzinfo=tz)
print("strictly-after(base=fire instant):", t2.get_next_fire_time(None, b3))

# interval drift check n/a; check step syntax and ranges
for expr in ("*/15 9-17 * * 1-5", "0 9 1 * *", "30 9,15 * * *", "0 0 * * 7"):
    try:
        CronTrigger.from_crontab(expr, timezone=tz)
        print("expr ok:", expr)
    except ValueError as e:
        print("expr rejected:", expr, e)
