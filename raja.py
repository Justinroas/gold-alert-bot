"""Raja strategy on XAU/USD 15-min candles (alerts only, never trades).

Built from Raja Banks' public material and screenshots of trades he took. Two setups,
both around key levels:

Key levels for today (each only used once it is complete):
  yesterday's high/low (from 6pm), Asia high/low (6pm-3am NY, usable from 3am),
  London high/low (3am-7am NY, usable from 7am).

Setup A, break & retest: a 15-min candle closes strongly through a high (BUY) or low (SELL),
  then within RETEST_BARS candles price comes back to the level and holds. Entry = that close.
  Cancelled if a candle closes back through the level first.

Setup B, rejection: price pokes into a high (or low) but the candle closes back below
  (above) it with a long wick, and the next candle confirms by closing beyond the rejection candle's
  low (high). Entry = the confirming close. (From Raja's 4,127 ceiling sell, Nov 10.)

Setup C, range breakout (Raja's video, Nov 4): after a big move, candles print side by
  side in a tight range ("CFR, clean range" behind it). Wait for a candle to break and CLOSE
  outside the range, then go with it. Entry = that close.

Stop loss: just past the setup's extreme, capped at MAX_STOP pips.
Take profit: TP1 / TP2 pips (1 pip = $0.10 on gold). Session: 6pm (Asia open) to noon New York time.
"""
import os
from datetime import timedelta, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
STRATEGY = "📗 Strategy: Raja strategy"
BAR = timedelta(minutes=15)
PIP = 0.10
TP1_PIPS = int(os.environ.get("RAJA_TP1_PIPS", "35"))
TP2_PIPS = int(os.environ.get("RAJA_TP2_PIPS", "55"))
MAX_STOP = float(os.environ.get("RAJA_MAX_STOP_PIPS", "50")) * PIP
STOP_BUFFER = 5 * PIP
TOUCH = 10 * PIP
BODY_MIN = 0.5
WICK_MIN = 0.4   # rejection candle: the wick into the level is at least 40% of the candle
RETEST_BARS = 4
BREAK_RETEST = os.environ.get("RAJA_BREAK_RETEST", "off") == "on"
BOX_BARS = 6          # range setup: 6 candles (1.5h) side by side
IMPULSE_BARS = 8      # the 2 hours before the range
IMPULSE_MULT = 2.0    # the move before the range is at least 2x the range height
MAX_RANGE = 200 * PIP # Raja's example range was 170 pips
START = time(int(os.environ.get("RAJA_START_HOUR", "18")), 0)   # 6pm: Asia session opens
END = time(int(os.environ.get("RAJA_END_HOUR", "12")), 0)


def trading_day(ts):
    """Gold's trading day starts at 6pm New York; a bar at 6pm or later belongs to the next day."""
    return (ts.astimezone(NY) + timedelta(hours=6)).date()


def _ny(ts):
    return ts.astimezone(NY).time()


def _hl(bars):
    return (max(b["high"] for b in bars), min(b["low"] for b in bars)) if bars else (None, None)


def key_levels(bars, day):
    """[(name, price, usable_from_NY_time)] for trading day `day`."""
    days = sorted({trading_day(b["start"]) for b in bars if trading_day(b["start"]) < day})
    prev = [b for b in bars if days and trading_day(b["start"]) == days[-1]]
    today = [b for b in bars if trading_day(b["start"]) == day]
    asia = [b for b in today if _ny(b["start"]) >= time(18) or _ny(b["start"]) < time(3)]
    london = [b for b in today if time(3) <= _ny(b["start"]) < time(7)]
    out = []
    for name, group, usable in (("yesterday's", prev, time(18)), ("Asia", asia, time(3)),
                                ("London", london, time(7))):
        hi, lo = _hl(group)
        if hi is not None:
            out.append((f"{name} high", hi, usable))
            out.append((f"{name} low", lo, usable))
    return out


def _mins(t):
    """Minutes since 6pm NY (the start of gold's trading day), so overnight times sort correctly."""
    return ((t.hour - 18) % 24) * 60 + t.minute


def _in_session(b):
    return _mins(START) < _mins(_ny(b["start"] + BAR)) <= _mins(END)


def _make(side, entry, extreme, at, levels, setup, detail):
    if side == "BUY":
        stop = max(extreme - STOP_BUFFER, entry - MAX_STOP)
        tp1, tp2 = entry + TP1_PIPS * PIP, entry + TP2_PIPS * PIP
    else:
        stop = min(extreme + STOP_BUFFER, entry + MAX_STOP)
        tp1, tp2 = entry - TP1_PIPS * PIP, entry - TP2_PIPS * PIP
    return dict(side=side, entry=entry, stop=stop, tp1=tp1, tp2=tp2, at=at,
                levels=levels, setup=setup, detail=detail)


def find_entries(bars, now):
    """All Raja entries for the current trading day, oldest first."""
    done = [b for b in bars if b["start"] + BAR <= now]
    if not done:
        return []
    day = trading_day(done[-1]["start"])
    levels = key_levels(done, day)
    today = [b for b in done if trading_day(b["start"]) == day]
    entries, used = {}, set()

    def add(e, name):
        used.add(name)
        k = (e["at"], e["side"])
        if k in entries:
            entries[k]["levels"] += e["levels"]
            if e["setup"] not in entries[k]["setup"]:
                entries[k]["setup"] += " + " + e["setup"]
        else:
            entries[k] = e

    for i in range(1, len(today)):
        b, prev = today[i], today[i - 1]
        if not _in_session(b):
            continue
        t = _mins(_ny(b["start"]))
        rng = b["high"] - b["low"]
        if rng <= 0:
            continue
        strong = abs(b["close"] - b["open"]) >= BODY_MIN * rng
        for name, lvl, usable in levels:
            if name in used or t < _mins(usable):
                continue
            is_high = name.endswith("high")
            # Setup A: break & retest
            side = None
            if strong and is_high and b["close"] > lvl > prev["close"] and b["close"] > b["open"]:
                side = "BUY"
            elif strong and not is_high and b["close"] < lvl < prev["close"] and b["close"] < b["open"]:
                side = "SELL"
            if side and not BREAK_RETEST:
                side = None  # setup A switched off: it lost more than it won in the replay
            if side:
                for r in today[i + 1:i + 1 + RETEST_BARS]:
                    if (side == "BUY" and r["close"] <= lvl) or (side == "SELL" and r["close"] >= lvl):
                        break
                    if (r["low"] <= lvl + TOUCH) if side == "BUY" else (r["high"] >= lvl - TOUCH):
                        at = (r["start"] + BAR).astimezone(NY)
                        way = "above" if side == "BUY" else "below"
                        add(_make(side, r["close"], r["low"] if side == "BUY" else r["high"], at,
                                  [(name, lvl)], "Break & retest",
                                  f"Broke {way} the level at {(b['start'] + BAR).astimezone(NY):%-I:%M %p}, "
                                  f"came back to it and held."), name)
                        break
                continue
            # Setup B: rejection, confirmed by the next candle
            if i + 1 >= len(today):
                continue
            c = today[i + 1]
            upper_wick = b["high"] - max(b["open"], b["close"])
            lower_wick = min(b["open"], b["close"]) - b["low"]
            if is_high and prev["close"] < lvl and b["high"] >= lvl - TOUCH and b["close"] < lvl \
                    and upper_wick >= WICK_MIN * rng and c["close"] < b["low"] and c["close"] < c["open"]:
                at = (c["start"] + BAR).astimezone(NY)
                add(_make("SELL", c["close"], max(b["high"], c["high"]), at, [(name, lvl)], "Rejection",
                          f"Price hit the level at {(b['start'] + BAR).astimezone(NY):%-I:%M %p}, "
                          f"got pushed back down, and the next candle confirmed."), name)
            elif not is_high and prev["close"] > lvl and b["low"] <= lvl + TOUCH and b["close"] > lvl \
                    and lower_wick >= WICK_MIN * rng and c["close"] > b["high"] and c["close"] > c["open"]:
                at = (c["start"] + BAR).astimezone(NY)
                add(_make("BUY", c["close"], min(b["low"], c["low"]), at, [(name, lvl)], "Rejection",
                          f"Price hit the level at {(b['start'] + BAR).astimezone(NY):%-I:%M %p}, "
                          f"got pushed back up, and the next candle confirmed."), name)
        # Setup C: tight range after a big move ("clean range"), break and close out of it
        if i >= BOX_BARS + IMPULSE_BARS and strong:
            box = today[i - BOX_BARS:i]
            top, bottom = max(x["high"] for x in box), min(x["low"] for x in box)
            height = top - bottom
            before = today[i - BOX_BARS - IMPULSE_BARS:i - BOX_BARS]
            move = abs(before[-1]["close"] - before[0]["open"])
            key = f"range {box[0]['start']:%H:%M}"
            if 0 < height <= MAX_RANGE and move >= IMPULSE_MULT * height and key not in used:
                side = "BUY" if b["close"] > top else "SELL" if b["close"] < bottom else None
                if side:
                    at = (b["start"] + BAR).astimezone(NY)
                    used.add(key)
                    lvl = top if side == "BUY" else bottom
                    add(_make(side, b["close"], b["low"] if side == "BUY" else b["high"], at,
                              [("range " + ("top" if side == "BUY" else "bottom"), lvl)], "Range breakout",
                              f"After a big move, price went sideways in a {height / PIP:.0f}-pip range, "
                              f"then a candle closed {'above' if side == 'BUY' else 'below'} it."), key)
    return [entries[k] for k in sorted(entries, key=lambda k: k[0])]


def entry_key(e):
    return f"{e['at']:%Y-%m-%d %H:%M} {e['side']}"


def format_raja(e):
    icon = "🟢" if e["side"] == "BUY" else "🔴"
    lv = " + ".join(f"{n} ${v:,.2f}" for n, v in e["levels"])
    stop_pips = abs(e["entry"] - e["stop"]) / PIP
    return (f"{STRATEGY}\n"
            f"{icon} XAU/USD {e['side']} ENTRY ({e['at']:%-I:%M %p} NY), {e['setup']}\n"
            f"Level: {lv}\n"
            f"{e['detail']}\n"
            f"Entry: ${e['entry']:,.2f}\n"
            f"Stop loss: ${e['stop']:,.2f} ({stop_pips:.0f} pips)\n"
            f"Take profit 1: ${e['tp1']:,.2f} ({TP1_PIPS} pips)\n"
            f"Take profit 2: ${e['tp2']:,.2f} ({TP2_PIPS} pips)")


def outcome(e, later):
    """What happened after the entry: 'TP2', 'TP1', 'TP1 then stop', 'SL', 'unclear' or 'open'."""
    hit_tp1 = False
    for b in later:
        if b["start"] < e["at"]:
            continue
        lo, hi = b["low"], b["high"]
        buy = e["side"] == "BUY"
        stopped = lo <= e["stop"] if buy else hi >= e["stop"]
        tp1 = hi >= e["tp1"] if buy else lo <= e["tp1"]
        tp2 = hi >= e["tp2"] if buy else lo <= e["tp2"]
        if stopped and (tp1 and not hit_tp1):
            return "unclear"  # stop and target in the same candle: can't tell which came first
        if stopped:
            return "TP1 then stop" if hit_tp1 else "SL"
        if tp2:
            return "TP2"
        hit_tp1 = hit_tp1 or tp1
    return "TP1" if hit_tp1 else "open"
