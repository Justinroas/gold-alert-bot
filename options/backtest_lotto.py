"""Replay a SPY same-day (0DTE) "lottery ticket": on the SPY opening-range breakout, buy a cheap
same-day option and see how often it doubles.

Yahoo has no history of option prices, so option prices are ESTIMATED with Black-Scholes:
volatility = VIX at entry, time left = minutes until 4pm. Real 0DTE options are often priced a bit
higher (bigger IV for far out strikes), so real results are likely somewhat worse than this.

Entry: the first 5-min close outside the first 15 minutes' range (9:45-11:30), +5 min for the bot's lag.
Strike: the out-of-the-money strike (whole dollars) whose estimated price is closest to the target premium.
Exits compared:
  A) sell at 2x, stop when SPY falls back to the middle of the range, else sell at 3pm
  B) sell at 5x, same stop, else 3pm
  C) no stop, sell at 2x or at 3pm
"""
import math
import os
import sys
from datetime import time, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from backtest_spy import by_day, load, vix_lookup  # noqa: E402
from options_bot import bs_price  # noqa: E402

TARGETS = [0.10, 0.18, 0.25]
YEAR_MIN = 365 * 24 * 60


def years_left(ts):
    close = ts.replace(hour=16, minute=0, second=0, microsecond=0)
    return max((close - ts).total_seconds() / 60, 1) / YEAR_MIN


def pick_strike(side, spot, t, vol, target):
    best = None
    k0 = math.floor(spot) + 1 if side == "call" else math.ceil(spot) - 1
    for i in range(0, 40):
        k = k0 + i if side == "call" else k0 - i
        p = bs_price(side, spot, k, t, vol)
        if best is None or abs(p - target) < abs(best[1] - target):
            best = (k, p)
    return best


def replay(days, vix, target, exit_mult, use_stop, cutoff=time(15, 0)):
    prev_close, intr, _, _ = vix
    out = []
    for d, bars in sorted(days.items()):
        if len(bars) < 10:
            continue
        rng = bars[:3]
        hi, lo = max(b[2] for b in rng), min(b[3] for b in rng)
        mid = (hi + lo) / 2
        for i in range(3, len(bars) - 2):
            ts, o, h, l, c = bars[i]
            if (ts + timedelta(minutes=5)).time() > time(11, 30):
                break
            side = "call" if c > hi else "put" if c < lo else None
            if not side:
                continue
            vnow = intr.get(ts) or max((v for k, v in intr.items() if k.date() == d and k <= ts), default=None) \
                if intr else None
            vprev = prev_close.get(d)
            agree = vnow is not None and vprev and ((side == "call" and vnow < vprev) or (side == "put" and vnow > vprev))
            vol = (vnow or 18) / 100
            ets, _, _, _, entry_spot = bars[i + 1]  # bot sees it about 5 minutes later
            entry_ts = ets + timedelta(minutes=5)
            k, prem = pick_strike(side, entry_spot, years_left(entry_ts), vol, target)
            if prem < 0.03:
                break
            result, peak = None, 1.0
            for ts2, o2, h2, l2, c2 in bars[i + 2:]:
                end = ts2 + timedelta(minutes=5)
                t = years_left(end)
                best_spot = h2 if side == "call" else l2
                worst_spot = l2 if side == "call" else h2
                val_best = bs_price(side, best_spot, k, t, vol)
                peak = max(peak, val_best / prem)
                if use_stop and ((side == "call" and worst_spot <= mid) or (side == "put" and worst_spot >= mid)):
                    result = bs_price(side, mid, k, t, vol) / prem
                    break
                if val_best >= exit_mult * prem:
                    result = exit_mult
                    break
                if end.time() >= cutoff:
                    result = bs_price(side, c2, k, t, vol) / prem
                    break
            if result is None:
                result = 0.0
            out.append(dict(day=d, side=side, agree=agree, mult=result, peak=peak, prem=prem))
            break
    return out


def row(name, tr):
    if not tr:
        return f"| {name} | 0 | | | | | |"
    n = len(tr)
    won = sum(t["mult"] >= 1.99 for t in tr)
    avg = sum(t["mult"] - 1 for t in tr) / n * 100
    p5 = sum(t["peak"] >= 5 for t in tr) / n * 100
    p10 = sum(t["peak"] >= 10 for t in tr) / n * 100
    zero = sum(t["mult"] < 0.2 for t in tr) / n * 100
    return f"| {name} | {n} | {won / n * 100:.0f}% | {avg:+.0f}% | {zero:.0f}% | {p5:.0f}% | {p10:.0f}% |"


def main():
    spy = by_day(load("SPY", "5m", "60d"))
    vix = vix_lookup(load("^VIX", "5m", "60d"), load("^VIX", "1d", "1y"))
    lines = ["## SPY 0DTE lottery replay (last ~60 trading days, option prices estimated)", "",
             "Won = sold at the target. Avg = average result per trade (+100% = doubled your money)."
             " Lost most = ended worth under 20% of what you paid. 5x/10x = the option hit that at some point.", ""]
    for target in TARGETS:
        lines += [f"### Option costing about ${target:.2f} (${target * 100:.0f} per contract)", "",
                  "| Version | Trades | Won | Avg | Lost most | Hit 5x | Hit 10x |", "|---|---|---|---|---|---|---|"]
        for name, mult, stop in (("A) 2x or stop or 3pm", 2, True), ("B) 5x or stop or 3pm", 5, True),
                                 ("C) 2x or 3pm, no stop", 2, False)):
            tr = replay(spy, vix, target, mult, stop)
            lines.append(row(f"{name}, all breakouts", tr))
            lines.append(row(f"{name}, VIX agrees", [t for t in tr if t["agree"]]))
        lines.append("")
    text = "\n".join(lines)
    print(text)
    s = os.environ.get("GITHUB_STEP_SUMMARY")
    if s:
        with open(s, "a") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
