"""Raja Banks style break-and-retest on XAU/USD 15-min candles (alerts only, never trades).

Rules (as we understood them; tweak the settings below):
1. Key levels for today: previous day high/low, Asia session high/low (6pm-3am NY),
   London session high/low (3am-7am NY).
2. Break: a finished 15-min candle closes through a level with a strong body
   (body >= BODY_MIN of the candle, in the breakout direction), and the candle before
   it closed on the other side of the level.
3. Retest: within RETEST_BARS candles, price comes back to the level (within TOUCH)
   and that candle closes back in the breakout direction (it holds). That close is the entry.
   If a candle closes back through the level first, the setup is cancelled.
4. Stop loss: just past the retest candle (STOP_BUFFER beyond its low/high), capped at
   MAX_STOP from entry. Take profit: TP1 = 35 pips, TP2 = 50 pips (1 pip = $0.10 on gold).
5. Breaks between 7:00am and 11:00am New York time. One entry per level per day.
"""
import os
from datetime import timedelta, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
STRATEGY = "📗 Strategy: Raja Banks break & retest (15-min)"
BAR = timedelta(minutes=15)
PIP = 0.10
TP1_PIPS = int(os.environ.get("RAJA_TP1_PIPS", "35"))
TP2_PIPS = int(os.environ.get("RAJA_TP2_PIPS", "50"))
MAX_STOP = float(os.environ.get("RAJA_MAX_STOP_PIPS", "50")) * PIP
STOP_BUFFER = 5 * PIP
TOUCH = 10 * PIP
BODY_MIN = 0.5
RETEST_BARS = 4
START = time(int(os.environ.get("RAJA_START_HOUR", "7")), 0)
END = time(int(os.environ.get("RAJA_END_HOUR", "11")), 0)


def trading_day(ts):
    """Gold's trading day starts at 6pm New York; a bar at 6pm or later belongs to the next day."""
    return (ts.astimezone(NY) + timedelta(hours=6)).date()


def _hl(bars):
    return (max(b["high"] for b in bars), min(b["low"] for b in bars)) if bars else (None, None)


def key_levels(bars, day):
    """Levels for trading day `day`, from bars before 7am NY that day."""
    days = sorted({trading_day(b["start"]) for b in bars if trading_day(b["start"]) < day})
    prev = [b for b in bars if days and trading_day(b["start"]) == days[-1]]
    today = [b for b in bars if trading_day(b["start"]) == day]
    asia = [b for b in today if b["start"].astimezone(NY).time() >= time(18) or b["start"].astimezone(NY).time() < time(3)]
    london = [b for b in today if time(3) <= b["start"].astimezone(NY).time() < time(7)]
    out = []
    for name, group in (("yesterday's", prev), ("Asia", asia), ("London", london)):
        hi, lo = _hl(group)
        if hi is not None:
            out.append((f"{name} high", hi))
            out.append((f"{name} low", lo))
    return out


def find_entries(bars, now):
    """All break-and-retest entries for the current trading day, oldest first."""
    done = [b for b in bars if b["start"] + BAR <= now]
    if not done:
        return []
    day = trading_day(done[-1]["start"])
    levels = key_levels(done, day)
    today = [b for b in done if trading_day(b["start"]) == day]
    entries, used = {}, set()
    for i in range(1, len(today)):
        b, prev = today[i], today[i - 1]
        close_ny = (b["start"] + BAR).astimezone(NY).time()
        if not (START < close_ny <= END):
            continue
        rng = b["high"] - b["low"]
        if rng <= 0 or abs(b["close"] - b["open"]) < BODY_MIN * rng:
            continue
        for name, lvl in levels:
            if name in used:
                continue
            if b["close"] > lvl and b["close"] > b["open"] and prev["close"] <= lvl:
                side = "BUY"
            elif b["close"] < lvl and b["close"] < b["open"] and prev["close"] >= lvl:
                side = "SELL"
            else:
                continue
            for r in today[i + 1:i + 1 + RETEST_BARS]:
                if (side == "BUY" and r["close"] <= lvl) or (side == "SELL" and r["close"] >= lvl):
                    break  # closed back through: failed break
                touched = r["low"] <= lvl + TOUCH if side == "BUY" else r["high"] >= lvl - TOUCH
                if touched:
                    used.add(name)
                    at = (r["start"] + BAR).astimezone(NY)
                    k = (at, side)
                    if k in entries:
                        entries[k]["levels"].append((name, lvl))
                        break
                    entry = r["close"]
                    if side == "BUY":
                        stop = max(r["low"] - STOP_BUFFER, entry - MAX_STOP)
                        tp1, tp2 = entry + TP1_PIPS * PIP, entry + TP2_PIPS * PIP
                    else:
                        stop = min(r["high"] + STOP_BUFFER, entry + MAX_STOP)
                        tp1, tp2 = entry - TP1_PIPS * PIP, entry - TP2_PIPS * PIP
                    entries[k] = dict(side=side, levels=[(name, lvl)], entry=entry, stop=stop,
                                      tp1=tp1, tp2=tp2, at=at,
                                      broke_at=(b["start"] + BAR).astimezone(NY))
                    break
    return [entries[k] for k in sorted(entries, key=lambda k: k[0])]


def entry_key(e):
    return f"{e['at']:%Y-%m-%d %H:%M} {e['side']}"


def format_raja(e):
    icon = "🟢" if e["side"] == "BUY" else "🔴"
    lv = " + ".join(f"{n} ${v:,.2f}" for n, v in e["levels"])
    way = "above" if e["side"] == "BUY" else "below"
    stop_pips = abs(e["entry"] - e["stop"]) / PIP
    return (f"{STRATEGY}\n"
            f"{icon} XAU/USD {e['side']} ENTRY ({e['at']:%-I:%M %p} NY)\n"
            f"Broke {way} {lv} at {e['broke_at']:%-I:%M %p}, retested and held.\n"
            f"Entry: ${e['entry']:,.2f}\n"
            f"Stop loss: ${e['stop']:,.2f} ({stop_pips:.0f} pips)\n"
            f"Take profit 1: ${e['tp1']:,.2f} ({TP1_PIPS} pips)\n"
            f"Take profit 2: ${e['tp2']:,.2f} ({TP2_PIPS} pips)")


def outcome(e, later):
    """What happened after the entry: 'TP2', 'TP1', 'SL' or 'open'. Same-candle ties count as SL."""
    hit_tp1 = False
    for b in later:
        if b["start"] < e["at"]:
            continue
        lo, hi = b["low"], b["high"]
        if e["side"] == "BUY":
            if lo <= e["stop"]:
                return "TP1 then stop" if hit_tp1 else "SL"
            if hi >= e["tp2"]:
                return "TP2"
            hit_tp1 = hit_tp1 or hi >= e["tp1"]
        else:
            if hi >= e["stop"]:
                return "TP1 then stop" if hit_tp1 else "SL"
            if lo <= e["tp2"]:
                return "TP2"
            hit_tp1 = hit_tp1 or lo <= e["tp1"]
    return "TP1" if hit_tp1 else "open"
