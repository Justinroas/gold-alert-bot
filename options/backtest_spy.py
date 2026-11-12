"""Replay a SPY opening-range breakout (the gold bot's breakout idea, applied to SPY)
on real past prices, with and without VIX filters. Prints a results table.

Rules tested (one trade per day, the first breakout only):
- Opening range (OR) = high and low of the first N minutes after 9:30am New York.
- CALL when a 5-min candle CLOSES above the OR high, PUT when one closes below the OR low,
  between the end of the OR and 11:30am.
- Exit A: target = 1 x OR height past the entry, stop = middle of the OR (whichever hits first),
  else exit at 3:55pm. Result in "R" (1R = the distance from entry to stop).
- Exit B: just hold to 3:55pm.
VIX filters:
- "VIX agrees": calls only when VIX is DOWN on the day at the signal, puts only when VIX is UP.
- VIX level buckets at the open.
A longer check uses 1-hour candles (about 2 years): OR = first hour, signal = hourly close
beyond it before 1:30pm, exit A/B the same way.
"""
import os
from collections import defaultdict
from datetime import time
from zoneinfo import ZoneInfo

import yfinance as yf

NY = ZoneInfo("America/New_York")


def load(ticker, interval, period):
    df = yf.download(ticker, period=period, interval=interval, auto_adjust=False, progress=False)
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.dropna()
    if df.index.tz is not None:  # daily candles have no time zone
        df.index = df.index.tz_convert(NY)
    return df


def by_day(df):
    days = defaultdict(list)
    for ts, r in df.iterrows():
        if time(9, 30) <= ts.time() < time(16, 0):
            days[ts.date()].append((ts, float(r.Open), float(r.High), float(r.Low), float(r.Close)))
    return days


def vix_lookup(vix_intraday, vix_daily):
    prev_close = {}
    dates = sorted(vix_daily.index.date)
    closes = dict(zip(vix_daily.index.date, vix_daily["Close"].astype(float)))
    for a, b in zip(dates, dates[1:]):
        prev_close[b] = closes[a]
    intr = {ts: float(r.Close) for ts, r in vix_intraday.iterrows()} if vix_intraday is not None else {}
    opens = {}
    if vix_intraday is not None:
        for ts, r in vix_intraday.iterrows():
            opens.setdefault(ts.date(), float(r.Open))
    daily_open = dict(zip(vix_daily.index.date, vix_daily["Open"].astype(float)))
    return prev_close, intr, opens, daily_open


def simulate(days, or_minutes, bar_minutes, last_signal, vix=None):
    trades = []
    prev_close, intr, vopen, vdaily_open = vix or ({}, {}, {}, {})
    for d, bars in sorted(days.items()):
        n_or = or_minutes // bar_minutes
        if len(bars) <= n_or + 1:
            continue
        orb = bars[:n_or]
        hi, lo = max(b[2] for b in orb), min(b[3] for b in orb)
        mid, height = (hi + lo) / 2, hi - lo
        if height <= 0:
            continue
        for i in range(n_or, len(bars)):
            ts, o, h, l, c = bars[i]
            close_time = (ts + __import__("datetime").timedelta(minutes=bar_minutes)).time()
            if close_time > last_signal:
                break
            side = "call" if c > hi else "put" if c < lo else None
            if not side:
                continue
            entry = c
            stop = mid
            risk = abs(entry - stop)
            target = entry + height if side == "call" else entry - height
            result_a = None
            for ts2, o2, h2, l2, c2 in bars[i + 1:]:
                if ts2.time() >= time(15, 55):
                    break
                if side == "call":
                    if l2 <= stop:
                        result_a = -1.0
                        break
                    if h2 >= target:
                        result_a = height / risk
                        break
                else:
                    if h2 >= stop:
                        result_a = -1.0
                        break
                    if l2 <= target:
                        result_a = height / risk
                        break
            last = [b for b in bars if b[0].time() < time(15, 55)][-1][4]
            move = (last - entry) if side == "call" else (entry - last)
            if result_a is None:
                result_a = move / risk
            vix_now = intr.get(ts)
            if vix_now is None and intr:  # hourly VIX bars may not line up exactly: use the last one before
                earlier = [k for k in intr if k.date() == d and k <= ts]
                vix_now = intr[max(earlier)] if earlier else None
            vix_prev = prev_close.get(d)
            vix_dir = None
            if vix_now is not None and vix_prev:
                vix_dir = "up" if vix_now > vix_prev else "down"
            vix_level = vopen.get(d) or vdaily_open.get(d)
            trades.append(dict(day=d, side=side, r=result_a, hold_pct=move / entry * 100, win_hold=move > 0,
                               vix_dir=vix_dir, vix_level=vix_level, or_pct=height / entry * 100))
            break
    return trades


def summarize(name, trades):
    if not trades:
        return f"| {name} | 0 | | | | |"
    n = len(trades)
    wins = sum(1 for t in trades if t["r"] > 0)
    avg_r = sum(t["r"] for t in trades) / n
    tot_r = sum(t["r"] for t in trades)
    hold_win = sum(1 for t in trades if t["win_hold"]) / n * 100
    avg_hold = sum(t["hold_pct"] for t in trades) / n
    return (f"| {name} | {n} | {wins / n * 100:.0f}% | {avg_r:+.2f}R | {tot_r:+.1f}R |"
            f" {hold_win:.0f}% / {avg_hold:+.2f}% |")


def report(title, trades):
    agree = [t for t in trades if (t["side"] == "call" and t["vix_dir"] == "down")
             or (t["side"] == "put" and t["vix_dir"] == "up")]
    disagree = [t for t in trades if t["vix_dir"] and t not in agree]
    rows = [
        summarize("All breakouts", trades),
        summarize("Calls only", [t for t in trades if t["side"] == "call"]),
        summarize("Puts only", [t for t in trades if t["side"] == "put"]),
        summarize("VIX agrees", agree),
        summarize("VIX disagrees", disagree),
    ]
    for lo, hi in ((0, 15), (15, 20), (20, 25), (25, 99)):
        rows.append(summarize(f"VIX {lo}-{hi} at open",
                              [t for t in trades if t["vix_level"] and lo <= t["vix_level"] < hi]))
        rows.append(summarize(f"VIX {lo}-{hi} + agrees",
                              [t for t in agree if t["vix_level"] and lo <= t["vix_level"] < hi]))
    first, last = (trades[0]["day"], trades[-1]["day"]) if trades else ("", "")
    out = [f"### {title} ({first} to {last})", "",
           "| Version | Trades | Win (target/stop) | Avg | Total | Hold to close: win / avg move |",
           "|---|---|---|---|---|---|", *rows, ""]
    return "\n".join(out)


def cell(trades):
    if not trades:
        return "-"
    n = len(trades)
    return f"{n} / {sum(1 for t in trades if t['r'] > 0) / n * 100:.0f}% / {sum(t['r'] for t in trades) / n:+.2f}R"


def compare(tickers, fear="^VIX"):
    """One row per ticker: trades / win % / average R for each version."""
    vixd = load(fear, "1d", "3y")
    v5 = vix_lookup(load(fear, "5m", "60d"), vixd)
    v1h = vix_lookup(load(fear, "1h", "730d"), vixd)
    rows = [f"### Opening-range breakout by ticker (fear gauge: {fear})", "",
            "Cells: trades / win % / avg R. Win = target before stop.", "",
            "| Ticker | 60d all | 60d fear agrees | 60d fear disagrees | 2y all | 2y fear agrees | 2y fear 20+ & agrees |",
            "|---|---|---|---|---|---|---|"]
    for tk in tickers:
        try:
            t5 = simulate(by_day(load(tk, "5m", "60d")), 15, 5, time(11, 30), v5)
            t1 = simulate(by_day(load(tk, "1h", "730d")), 60, 60, time(13, 30), v1h)
        except Exception as e:
            rows.append(f"| {tk} | error: {e} | | | | | |")
            continue
        ag = lambda ts: [t for t in ts if (t["side"] == "call" and t["vix_dir"] == "down")
                         or (t["side"] == "put" and t["vix_dir"] == "up")]
        dis = lambda ts: [t for t in ts if t["vix_dir"] and t not in ag(ts)]
        rows.append(f"| {tk} | {cell(t5)} | {cell(ag(t5))} | {cell(dis(t5))} | {cell(t1)} | {cell(ag(t1))} |"
                    f" {cell([t for t in ag(t1) if t['vix_level'] and t['vix_level'] >= 20])} |")
    return "\n".join(rows) + "\n"


def main():
    tickers = os.environ.get("TICKERS")
    if tickers:
        text = compare(tickers.split(",")) + "\n" + compare(["GLD"], fear="^GVZ")
        print(text)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as f:
                f.write(text + "\n")
        return
    spy5 = load("SPY", "5m", "60d")
    vix5 = load("^VIX", "5m", "60d")
    vixd = load("^VIX", "1d", "3y")
    spy1h = load("SPY", "1h", "730d")
    vix1h = load("^VIX", "1h", "730d")
    v5 = vix_lookup(vix5, vixd)
    v1h = vix_lookup(vix1h, vixd)
    d5 = by_day(spy5)
    d1h = by_day(spy1h)
    parts = ["## SPY opening-range breakout replay", ""]
    for orm in (15, 30):
        parts.append(report(f"5-min candles, {orm}-min opening range, signals until 11:30am",
                            simulate(d5, orm, 5, time(11, 30), v5)))
    # Hourly bars from Yahoo start at 9:30, so the first bar is the first hour.
    parts.append(report("1-hour candles, first-hour range, signals until 1:30pm (about 2 years)",
                        simulate(d1h, 60, 60, time(13, 30), v1h)))
    text = "\n".join(parts)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
