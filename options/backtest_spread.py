"""Small-budget replay: the panic-dip signal (tested in backtest_daily.py) played with about $100.

Signal: close above its 200-day average, RSI-2 under 10, VIX 20+. Buy at the close.
Exit: first close above the 5-day average, or after 10 trading days.

Two ways to spend about $100 (one contract, option prices estimated with Black-Scholes):
  SPREAD = buy the call at the money, sell a call a few dollars higher, ~3 weeks out.
           Width picked so the cost is under BUDGET. Max win = width - cost.
  CALL   = one call at the money ~3 weeks out (only fits cheap stocks).
Volatility: VIX for index ETFs; the stock's 20-day realized vol x 1.2 for stocks (floor 25%).
Costs: 5 cents per contract per leg, each way (bid/ask), so a spread pays 20 cents round trip.
"""
import math
import os

import yfinance as yf

INDEX = {"SPY", "QQQ", "IWM", "DIA"}
BUDGET = float(os.environ.get("BUDGET", "100"))
DAYS_OUT = 21
SLIP = 0.05
YEARS = os.environ.get("YEARS", "10y")


def load(t):
    df = yf.download(t, period=YEARS, interval="1d", auto_adjust=True, progress=False)
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.dropna()
    return [(ts.date(), float(r.Close)) for ts, r in df.iterrows()]


def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_call(s, k, t, v, r=0.04):
    if t <= 0:
        return max(s - k, 0.0)
    d1 = (math.log(s / k) + (r + v * v / 2) * t) / (v * math.sqrt(t))
    return s * ncdf(d1) - k * math.exp(-r * t) * ncdf(d1 - v * math.sqrt(t))


def rsi2(closes):
    out, up, dn = [None] * len(closes), None, None
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g, l = max(ch, 0), max(-ch, 0)
        up, dn = (g, l) if up is None else ((up + g) / 2, (dn + l) / 2)
        out[i] = 100 if dn == 0 else 100 - 100 / (1 + up / dn)
    return out


def strike_step(sym, price):
    if sym in INDEX:
        return 1.0
    return 0.5 if price < 25 else 1.0 if price < 100 else 2.5 if price < 250 else 5.0


def vol(sym, closes, i, vix):
    if sym in INDEX:
        return max(vix / 100 * (1.25 if sym == "IWM" else 1.0), 0.12)
    rets = [math.log(closes[k] / closes[k - 1]) for k in range(i - 19, i + 1)]
    m = sum(rets) / 20
    rv = math.sqrt(sum((x - m) ** 2 for x in rets) / 19 * 252)
    return max(rv * 1.2, 0.25)


def replay(sym, bars, vix):
    closes = [c for _, c in bars]
    rsi = rsi2(closes)
    out = dict(spread=[], call=[])
    i = 210
    while i < len(bars) - 1:
        d, s = bars[i]
        v20 = vix.get(d)
        if not (v20 and v20 >= 20 and s > sum(closes[i - 199:i + 1]) / 200 and rsi[i] is not None and rsi[i] < 10):
            i += 1
            continue
        j = i
        for j in range(i + 1, min(i + 11, len(bars))):
            if closes[j] > sum(closes[j - 4:j + 1]) / 5:
                break
        iv, ivx = vol(sym, closes, i, v20), vol(sym, closes, j, vix.get(bars[j][0], v20))
        t0, t1 = DAYS_OUT / 365, max(DAYS_OUT - (bars[j][0] - d).days, 0) / 365
        st = strike_step(sym, s)
        k1 = round(s / st) * st
        # spread: widest that fits the budget
        best = None
        for w_steps in range(1, 30):
            k2 = k1 + w_steps * st
            cost = bs_call(s, k1, t0, iv) - bs_call(s, k2, t0, iv) + 2 * SLIP
            if cost * 100 > BUDGET:
                break
            best = (k2, cost)
        if best:
            k2, cost = best
            val = max(bs_call(closes[j], k1, t1, ivx) - bs_call(closes[j], k2, t1, ivx) - 2 * SLIP, 0)
            out["spread"].append(dict(day=d, cost=cost * 100, pnl=(val - cost) * 100, width=k2 - k1))
        c = bs_call(s, k1, t0, iv) + SLIP
        if c * 100 <= BUDGET:
            val = max(bs_call(closes[j], k1, t1, ivx) - SLIP, 0)
            out["call"].append(dict(day=d, cost=c * 100, pnl=(val - c) * 100))
        i = j + 1
    return out


def summ(tr):
    if not tr:
        return "-"
    n = len(tr)
    w = sum(t["pnl"] > 0 for t in tr)
    h = n // 2
    half = lambda x: f"{sum(t['pnl'] > 0 for t in x) / len(x) * 100:.0f}%" if x else "-"
    return (f"{n} trades, {w / n * 100:.0f}% win, avg cost ${sum(t['cost'] for t in tr) / n:.0f},"
            f" avg P/L ${sum(t['pnl'] for t in tr) / n:+.0f}, total ${sum(t['pnl'] for t in tr):+.0f},"
            f" worst ${min(t['pnl'] for t in tr):+.0f} | halves {half(tr[:h])} / {half(tr[h:])}"
            f" | last {tr[-1]['day']}")


def main():
    tickers = (os.environ.get("TICKERS") or
               "SPY,QQQ,IWM,AAPL,MSFT,AMZN,GOOGL,META,NVDA,AMD,TSLA,PLTR,SOFI,F,INTC,BAC,T,PFE,HOOD,UBER").split(",")
    vix = dict(load("^VIX"))
    lines = [f"## Panic dip with ${BUDGET:.0f} ({YEARS}, estimated option prices)", ""]
    for t in tickers:
        try:
            r = replay(t, load(t), vix)
        except Exception as e:
            lines.append(f"{t}: failed {e}")
            continue
        lines.append(f"**{t}** SPREAD: {summ(r['spread'])}")
        lines.append(f"**{t}** CALL: {summ(r['call'])}")
        lines.append("")
    text = "\n".join(lines)
    print(text)
    s = os.environ.get("GITHUB_STEP_SUMMARY")
    if s:
        with open(s, "a") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
