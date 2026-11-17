"""One-off setups the user asked to watch (alerts only). Each has an expiry and fires once.
Add entries to WATCHES; old ones just expire."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
BAR = timedelta(minutes=15)

WATCHES = [
    dict(id="2026-11-17-pullback-sell", side="SELL", start="2026-11-17T13:45", expires="2026-11-17T16:00",
         kind="pullback", zone=(3863.0, 3866.0), stop=3872.0, targets=(3848.0, 3840.0),
         name="Pullback sell", how="Price bounced up to $3,863-3,866 and a 15-min candle closed back down."),
    dict(id="2026-11-17-breakdown-sell", side="SELL", start="2026-11-17T13:45", expires="2026-11-17T16:00",
         kind="close_below", level=3838.0, stop=3848.0, targets=(3825.0,),
         name="Breakdown sell", how="A 15-min candle closed below $3,838."),
]


def _t(s):
    return datetime.fromisoformat(s).replace(tzinfo=NY)


def check(bars15, now, fired):
    """Returns [(watch, entry_price, candle_close_time)] that trigger now and weren't sent before."""
    out = []
    for w in WATCHES:
        if w["id"] in fired or now > _t(w["expires"]) + BAR:
            continue
        done = [b for b in bars15 if b["start"] >= _t(w["start"]) and b["start"] + BAR <= now
                and b["start"] + BAR <= _t(w["expires"]) + BAR]
        touched = False
        for b in done:
            if w["side"] == "SELL" and b["close"] >= w["stop"]:
                break  # went through the stop level first: setup is dead
            if w["kind"] == "pullback":
                lo, hi = w["zone"]
                touched = touched or b["high"] >= lo
                hit = touched and b["close"] < b["open"] and b["close"] < lo
            else:
                hit = b["close"] < w["level"]
            if hit:
                if now - (b["start"] + BAR) <= timedelta(minutes=30):
                    out.append((w, b["close"], b["start"] + BAR))
                break
    return out


def format_watch(w, price, at):
    pips = lambda a, b: abs(a - b) / 0.10
    tps = " / ".join(f"${t:,.2f} ({pips(price, t):.0f} pips)" for t in w["targets"])
    return (f"🎯 Your setup: {w['name']} ({at.astimezone(NY):%-I:%M %p} NY)\n"
            f"🔴 XAU/USD {w['side']} NOW\n{w['how']}\n"
            f"Entry: ${price:,.2f}\nStop loss: ${w['stop']:,.2f} ({pips(price, w['stop']):.0f} pips)\n"
            f"Take profit: {tps}\nSize: 0.01 lot ($0.10 per pip)")
