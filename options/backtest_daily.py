"""Replay well-known daily strategies on 10 years of prices, to find ones worth options alerts.

Each strategy enters at the day's close and holds until its exit rule (max HOLD days).
Reported per ticker and strategy:
  trades, win % (closed higher for longs), average return, average days held,
  "pop %" = trades where the stock went at least 2% (1% for index ETFs) our way at some close
  while we held it (that's when a call/put usually pays), and the same numbers for the
  first and second half of the history (an edge should show up in both).
Baseline = buying on every day and holding 5 days, to compare against.
"""
import math
import os

import yfinance as yf

INDEX = {"SPY", "QQQ", "IWM", "DIA", "GLD"}
YEARS = os.environ.get("YEARS", "10y")


def load(t):
    df = yf.download(t, period=YEARS, interval="1d", auto_adjust=True, progress=False)
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.dropna()
    return [(ts.date(), float(r.Open), float(r.High), float(r.Low), float(r.Close)) for ts, r in df.iterrows()]


def sma(xs, n, i):
    return sum(xs[i - n + 1:i + 1]) / n if i >= n - 1 else None


def rsi2(closes):
    out = [None] * len(closes)
    up = dn = None
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g, l = max(ch, 0), max(-ch, 0)
        if up is None:
            up, dn = g, l
        else:
            up = (up * 1 + g) / 2
            dn = (dn * 1 + l) / 2
        out[i] = 100 if dn == 0 else 100 - 100 / (1 + up / dn)
    return out


def run(bars, vix, entry_fn, exit_fn, side="long", hold=10, pop=0.02):
    closes = [b[4] for b in bars]
    rsi = rsi2(closes)
    vmap = dict(vix)
    trades, i = [], 210
    while i < len(bars) - 1:
        ctx = dict(i=i, c=closes, rsi=rsi, vix=vmap.get(bars[i][0]), vixs=vix, bars=bars)
        if not entry_fn(ctx):
            i += 1
            continue
        entry = closes[i]
        best = 0.0
        j = i
        for j in range(i + 1, min(i + 1 + hold, len(bars))):
            mv = (closes[j] / entry - 1) * (1 if side == "long" else -1)
            best = max(best, mv)
            if exit_fn(dict(i=j, c=closes, rsi=rsi, entry=entry, start=i)):
                break
        ret = (closes[j] / entry - 1) * (1 if side == "long" else -1)
        trades.append(dict(day=bars[i][0], ret=ret, days=j - i, pop=best >= pop))
        i = j + 1
    return trades


def stats(tr):
    if not tr:
        return "-"
    n = len(tr)
    return (f"{n} / {sum(t['ret'] > 0 for t in tr) / n * 100:.0f}% / {sum(t['ret'] for t in tr) / n * 100:+.2f}%"
            f" / {sum(t['days'] for t in tr) / n:.1f}d / pop {sum(t['pop'] for t in tr) / n * 100:.0f}%")


def halves(tr):
    if len(tr) < 4:
        return "-", "-"
    h = len(tr) // 2
    f = lambda x: f"{sum(t['ret'] > 0 for t in x) / len(x) * 100:.0f}% / {sum(t['ret'] for t in x) / len(x) * 100:+.2f}%"
    return f(tr[:h]), f(tr[h:])


def strategies():
    up_trend = lambda x: x["c"][x["i"]] > sma(x["c"], 200, x["i"])
    dn_trend = lambda x: x["c"][x["i"]] < sma(x["c"], 200, x["i"])
    back_above_5 = lambda x: x["c"][x["i"]] > sma(x["c"], 5, x["i"])
    back_below_5 = lambda x: x["c"][x["i"]] < sma(x["c"], 5, x["i"])

    def vix_spike(x):
        v = x["vix"]
        if v is None:
            return False
        vs = [y for d, y in x["vixs"] if d <= x["bars"][x["i"]][0]][-11:-1]
        return len(vs) == 10 and v > 1.25 * sum(vs) / 10

    def three_down(x):
        c, i = x["c"], x["i"]
        return c[i] < c[i - 1] < c[i - 2] < c[i - 3]

    def breakout20(x):
        c, i = x["c"], x["i"]
        return c[i] > max(c[i - 20:i])

    def breakdown20(x):
        c, i = x["c"], x["i"]
        return c[i] < min(c[i - 20:i])

    def below10low(x):
        c, i = x["c"], x["i"]
        return c[i] < min(c[i - 10:i])

    def above10high(x):
        c, i = x["c"], x["i"]
        return c[i] > max(c[i - 10:i])

    return [
        ("Dip buy: RSI2<10 in uptrend -> CALL", lambda x: up_trend(x) and x["rsi"][x["i"]] < 10, back_above_5, "long"),
        ("Dip buy + VIX 20+ -> CALL", lambda x: up_trend(x) and x["rsi"][x["i"]] < 10 and (x["vix"] or 0) >= 20,
         back_above_5, "long"),
        ("3 down days in uptrend -> CALL", lambda x: up_trend(x) and three_down(x), back_above_5, "long"),
        ("VIX spike (25% over 10d avg) in uptrend -> CALL", lambda x: up_trend(x) and vix_spike(x), back_above_5, "long"),
        ("Rip sell: RSI2>90 in downtrend -> PUT", lambda x: dn_trend(x) and x["rsi"][x["i"]] > 90, back_below_5, "short"),
        ("20-day high breakout -> CALL", breakout20, below10low, "long"),
        ("20-day low breakdown -> PUT", breakdown20, above10high, "short"),
    ]


def main():
    tickers = os.environ.get("TICKERS", "SPY,QQQ,IWM,GLD,NVDA,AAPL,AMD,TSLA,META,MSFT,AMZN,GOOGL,PLTR").split(",")
    vix = [(b[0], b[4]) for b in load("^VIX")]
    out = [f"## Daily strategy replay ({YEARS})", "",
           "Cells: trades / win % / avg return / avg days held / pop % (moved 2%+ our way, 1% for ETFs).",
           "Halves: win % / avg return in the first and second half of the trades.", ""]
    for t in tickers:
        bars = load(t)
        pop = 0.01 if t in INDEX else 0.02
        closes = [b[4] for b in bars]
        base = []
        for i in range(210, len(bars) - 5):
            r = closes[i + 5] / closes[i] - 1
            base.append(dict(ret=r, days=5, pop=max(closes[k] / closes[i] - 1 for k in range(i + 1, i + 6)) >= pop))
        out += [f"### {t} (baseline, any day, hold 5 days: {stats(base)})", "",
                "| Strategy | Result | 1st half | 2nd half |", "|---|---|---|---|"]
        for name, ent, ex, side in strategies():
            tr = run(bars, vix, ent, ex, side=side, pop=pop)
            a, b = halves(tr)
            out.append(f"| {name} | {stats(tr)} | {a} | {b} |")
        out.append("")
    text = "\n".join(out)
    print(text)
    s = os.environ.get("GITHUB_STEP_SUMMARY")
    if s:
        with open(s, "a") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
