"""Print today's XAU/USD 5-min candles (New York time) so we can check what happened after an alert."""
import os
from zoneinfo import ZoneInfo

import alert

NY = ZoneInfo("America/New_York")
bars = alert._spot_bars(os.environ["TWELVEDATA_API_KEY"], "5min", int(os.environ.get("BARS", "200")))
for b in bars:
    print(f"{b['start'].astimezone(NY):%a %H:%M}  O {b['open']:.2f}  H {b['high']:.2f}  L {b['low']:.2f}  C {b['close']:.2f}")

# ---- live snapshot: where price is vs the levels both strategies watch ----
from datetime import datetime
import raja

now = datetime.now(tz=ZoneInfo("UTC"))
b15 = alert._spot_bars(os.environ["TWELVEDATA_API_KEY"], "15min", 250)
b30 = alert._spot_bars(os.environ["TWELVEDATA_API_KEY"], "30min", 100)
done15 = [b for b in b15 if b["start"] + raja.BAR <= now]
day = raja.trading_day(done15[-1]["start"])
price = b15[-1]["close"]
print(f"\nSNAPSHOT {now.astimezone(NY):%a %H:%M} NY  price {price:.2f}  session {raja.session_name(now)}")
for name, lvl, usable in raja.key_levels(done15, day):
    print(f"  level {name:18s} {lvl:9.2f}  ({(lvl - price) / raja.PIP:+.0f} pips from price, usable from {usable:%H:%M})")
done30 = [b for b in b30 if b["start"] + alert.BAR <= now]
box = done30[-alert.BOX_BARS:]
hist = done30[-alert.AVG_BARS - 1:-1]
avg = sum(b["high"] - b["low"] for b in hist) / len(hist)
top, bot = max(b["high"] for b in box), min(b["low"] for b in box)
print(f"  30-min box (last {alert.BOX_BARS}): {bot:.2f} - {top:.2f}  height {top - bot:.2f} vs tight limit {alert.TIGHT_MULT * avg:.2f}"
      f"  -> {'TIGHT' if top - bot <= alert.TIGHT_MULT * avg else 'not tight'}")
rb = done15[-raja.BOX_BARS:]
rt, rbm = max(b["high"] for b in rb), min(b["low"] for b in rb)
before = done15[-raja.BOX_BARS - raja.IMPULSE_BARS:-raja.BOX_BARS]
move = before[-1]["close"] - before[0]["open"]
print(f"  Raja range (last 6x15m): {rbm:.2f} - {rt:.2f} height {(rt - rbm) / raja.PIP:.0f} pips; move before it {move:+.2f}"
      f" (needs {raja.IMPULSE_MULT}x height = {raja.IMPULSE_MULT * (rt - rbm):.2f})")
print("  entries today so far:", [(e['at'].strftime('%H:%M'), e['side'], e['setup']) for e in raja.find_entries(b15, now)])
for b in done15[-8:]:
    print(f"  {b['start'].astimezone(NY):%H:%M} O {b['open']:.2f} H {b['high']:.2f} L {b['low']:.2f} C {b['close']:.2f}")
