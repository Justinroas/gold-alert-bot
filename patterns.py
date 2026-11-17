"""Chart-pattern strategy from the strategy video (alerts only, never trades). 15-min candles.

The video shows 4 chart pictures and says buy or sell for each:
1. Uptrend (higher highs and higher lows) + price pulls back into the order block -> BUY.
   (Mirror: downtrend + pullback up into the order block -> SELL.)
2. Liquidity sweep: price pokes above a recent high, then breaks structure (closes below the
   last swing low), then comes back up into the order block -> SELL. (Mirror for buys.)
3. Double top: two highs at about the same price, then a candle closes below the support
   between them -> SELL. (Mirror: double bottom, close above resistance -> BUY.)

Order block = the last opposite-colour candle before the strong move that broke structure.
Stop loss just past the order block (or the second top), capped at MAX_STOP pips, then
two take profits in pips.

The video was made for EUR/USD (the euro), so Sensei watches EUR/USD by default
(PATTERN_SYMBOL). The alerts still go to the Gold Alerts group, clearly marked 💶 EUR/USD.
"""
import os
from datetime import time, timedelta
from zoneinfo import ZoneInfo

import raja

NY = ZoneInfo("America/New_York")
STRATEGY = "📙 Strategy: Sensei"
SYMBOL = os.environ.get("PATTERN_SYMBOL", "EUR/USD")
EURO = SYMBOL == "EUR/USD"
LABEL = "💶 EUR/USD (euro, not gold)" if EURO else "XAU/USD"
PIP = 0.0001 if EURO else 0.10
TP1_PIPS = float(os.environ.get("PATTERN_TP1_PIPS", "15" if EURO else "35"))
TP2_PIPS = float(os.environ.get("PATTERN_TP2_PIPS", "30" if EURO else "55"))
MAX_STOP_PIPS = float(os.environ.get("PATTERN_MAX_STOP_PIPS", "20" if EURO else "50"))
STOP_BUFFER_PIPS = 2 if EURO else 5
ENABLED = os.environ.get("PATTERNS", "on") == "on"
# Setups that send alerts. The 53-day replay: double top +65 pips, liquidity sweep +65,
# double bottom -140, trend + order block too few to tell. Others still run in the replay.
LIVE_SETUPS = set(os.environ.get("PATTERN_SETUPS", "Double top,Liquidity sweep + order block").split(","))
BAR = timedelta(minutes=15)
SWING = 2                 # a swing high is higher than the 2 candles on each side
LOOKBACK = 64             # candles of history to look at (16 hours)
OB_MAX_AGE = 16           # an order block is only good for 4 hours
DOUBLE_TOL = 0.35         # two tops count as "equal" within 35% of an average candle
DOUBLE_MIN_GAP = 4        # at least 1 hour between the two tops
START = time(int(os.environ.get("PATTERNS_START_HOUR", "8")), 0)   # New York session tested better than London
END = time(int(os.environ.get("PATTERNS_END_HOUR", "16")), 0)


def swings(bars):
    """Indexes of confirmed swing highs and swing lows."""
    hi, lo = [], []
    for i in range(SWING, len(bars) - SWING):
        win = bars[i - SWING:i + SWING + 1]
        if bars[i]["high"] == max(b["high"] for b in win):
            hi.append(i)
        if bars[i]["low"] == min(b["low"] for b in win):
            lo.append(i)
    return hi, lo


def _order_block(bars, start, end, side):
    """Last bearish candle (for a BUY) / bullish candle (for a SELL) in bars[start:end]."""
    for k in range(end - 1, start - 1, -1):
        b = bars[k]
        if (side == "BUY" and b["close"] < b["open"]) or (side == "SELL" and b["close"] > b["open"]):
            return k
    return None


def in_hours(now):
    """True while Sensei is watching (weekdays, START to END New York time, plus 15 min)."""
    t = now.astimezone(NY)
    return t.weekday() < 5 and START <= t.time() <= (END if END.hour == 23 else time(END.hour, 20))


def _in_session(b):
    t = (b["start"] + BAR).astimezone(NY).time()
    return START < t <= END


def px(v):
    """Price as text: euro to 5 decimals, gold in dollars."""
    return f"{v:.5f}" if EURO else f"${v:,.2f}"


def _entry(side, entry, extreme, at, setup, detail):
    buf, cap = STOP_BUFFER_PIPS * PIP, MAX_STOP_PIPS * PIP
    if side == "BUY":
        stop = max(extreme - buf, entry - cap)
        tp1, tp2 = entry + TP1_PIPS * PIP, entry + TP2_PIPS * PIP
    else:
        stop = min(extreme + buf, entry + cap)
        tp1, tp2 = entry - TP1_PIPS * PIP, entry - TP2_PIPS * PIP
    return dict(side=side, entry=entry, stop=stop, tp1=tp1, tp2=tp2, at=at,
                levels=[], setup=setup, detail=detail)


def check_last(bars):
    """Setups that complete on the last candle of `bars` (all finished). Returns a list."""
    if len(bars) < 30:
        return []
    last = bars[-1]
    if not _in_session(last):
        return []
    i = len(bars) - 1
    hist = bars[:-1]
    hi, lo = swings(hist)
    avg = sum(b["high"] - b["low"] for b in bars[-21:-1]) / 20
    at = (last["start"] + BAR).astimezone(NY)
    out = []

    # Setup 3: double top / double bottom, confirmed by a close through the middle swing
    if len(hi) >= 2:
        a, b = hi[-2], hi[-1]
        mids = [k for k in lo if a < k < b]
        if b - a >= DOUBLE_MIN_GAP and mids and abs(hist[a]["high"] - hist[b]["high"]) <= DOUBLE_TOL * avg:
            neck = min(hist[k]["low"] for k in mids)
            top = max(hist[a]["high"], hist[b]["high"])
            if last["close"] < neck <= bars[-2]["close"] and max(x["high"] for x in bars[b + 1:]) < top:
                out.append(_entry("SELL", last["close"], top, at, "Double top",
                                  f"Two tops at about {px(top)}, then a candle closed below the support {px(neck)}."))
    if len(lo) >= 2:
        a, b = lo[-2], lo[-1]
        mids = [k for k in hi if a < k < b]
        if b - a >= DOUBLE_MIN_GAP and mids and abs(hist[a]["low"] - hist[b]["low"]) <= DOUBLE_TOL * avg:
            neck = max(hist[k]["high"] for k in mids)
            bottom = min(hist[a]["low"], hist[b]["low"])
            if last["close"] > neck >= bars[-2]["close"] and min(x["low"] for x in bars[b + 1:]) > bottom:
                out.append(_entry("BUY", last["close"], bottom, at, "Double bottom",
                                  f"Two bottoms at about {px(bottom)}, then a candle closed above the resistance {px(neck)}."))

    # Setups 1 and 2: pullback into an order block
    for side in ("BUY", "SELL"):
        ob = _find_ob(hist, hi, lo, side)
        if not ob:
            continue
        k, kind = ob
        if i - k > OB_MAX_AGE:
            continue
        z_hi, z_lo = hist[k]["high"], hist[k]["low"]
        later = bars[k + 1:-1]
        if side == "BUY":
            touched_before = any(x["low"] <= z_hi for x in later[2:])
            if not touched_before and last["low"] <= z_hi and last["close"] > z_hi and last["close"] > last["open"]:
                out.append(_entry("BUY", last["close"], z_lo, at, kind,
                                  f"Price came back to the order block {px(z_lo)}-{px(z_hi)} and bounced."))
        else:
            touched_before = any(x["high"] >= z_lo for x in later[2:])
            if not touched_before and last["high"] >= z_lo and last["close"] < z_lo and last["close"] < last["open"]:
                out.append(_entry("SELL", last["close"], z_hi, at, kind,
                                  f"Price came back to the order block {px(z_lo)}-{px(z_hi)} and got rejected."))
    return out


def _find_ob(bars, hi, lo, side):
    """Order block behind the latest break of structure in `side`'s direction.
    Returns (index, setup name) or None."""
    if len(hi) < 2 or len(lo) < 2:
        return None
    if side == "BUY":
        # break of structure up: a close above the last swing high
        h = hi[-1]
        brk = next((j for j in range(h + 1, len(bars)) if bars[j]["close"] > bars[h]["high"]), None)
        if brk is None:
            return None
        k = _order_block(bars, max(lo[-1] if lo[-1] < brk else h, h - 8), brk, "BUY")
        if k is None:
            return None
        uptrend = bars[hi[-1]]["high"] > bars[hi[-2]]["high"] and bars[lo[-1]]["low"] > bars[lo[-2]]["low"]
        swept = bars[lo[-1]]["low"] < bars[lo[-2]]["low"]
        if uptrend:
            return k, "Uptrend + order block"
        if swept:
            return k, "Liquidity sweep + order block"
        return None
    l = lo[-1]
    brk = next((j for j in range(l + 1, len(bars)) if bars[j]["close"] < bars[l]["low"]), None)
    if brk is None:
        return None
    k = _order_block(bars, max(hi[-1] if hi[-1] < brk else l, l - 8), brk, "SELL")
    if k is None:
        return None
    downtrend = bars[hi[-1]]["high"] < bars[hi[-2]]["high"] and bars[lo[-1]]["low"] < bars[lo[-2]]["low"]
    swept = bars[hi[-1]]["high"] > bars[hi[-2]]["high"]
    if downtrend:
        return k, "Downtrend + order block"
    if swept:
        return k, "Liquidity sweep + order block"
    return None


def find_entries(bars, now):
    """Pattern entries on recent finished candles (latest last). Used by the live bot and the replay."""
    done = [b for b in bars if b["start"] + BAR <= now]
    out = []
    for n in range(max(30, len(done) - 3), len(done) + 1):
        out += check_last(done[max(0, n - LOOKBACK):n])
    return out


def entry_key(e):
    return f"P {e['at']:%Y-%m-%d %H:%M} {e['side']}"


def format_pattern(e):
    icon = "🟢" if e["side"] == "BUY" else "🔴"
    stop_pips = abs(e["entry"] - e["stop"]) / PIP
    return (f"{STRATEGY} · {raja.session_name(e['at'])}\n"
            f"{icon} {LABEL} {e['side']} ENTRY ({e['at']:%-I:%M %p} NY), {e['setup']}\n"
            f"{e['detail']}\n"
            f"Entry: {px(e['entry'])}\n"
            f"Stop loss: {px(e['stop'])} ({stop_pips:.0f} pips)\n"
            f"Take profit 1: {px(e['tp1'])} ({TP1_PIPS:.0f} pips)\n"
            f"Take profit 2: {px(e['tp2'])} ({TP2_PIPS:.0f} pips)")
