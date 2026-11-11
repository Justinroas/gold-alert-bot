"""Replay the alert rules over recent real prices and report how often they would have fired.

Runs on GitHub Actions (needs TWELVEDATA_API_KEY). Writes a summary to the job page.
Volume comes from gold futures, which Yahoo only keeps for about 60 days of 30-min data,
so the replay covers roughly the last 60 days.
"""
import os
from datetime import timedelta

import alert
import raja


def replay(bars, tight, impulse):
    alert.TIGHT_MULT, alert.IMPULSE_MULT = tight, impulse
    alerts = []
    for i in range(alert.BOX_BARS + alert.AVG_BARS + 1, len(bars)):
        a = alert.check(bars[:i], bars[i - 1]["start"] + alert.BAR)
        if not a:
            continue
        nxt = bars[i:i + 2]            # the next hour: did price break the entry level?
        later = bars[i:i + 4]          # the next two hours: how far did price go each way?
        if a["side"] == "BUY":
            entered = any(b["high"] > a["entry"] for b in nxt)
            best = max((b["high"] for b in later), default=a["entry"]) - a["entry"]
            worst = a["entry"] - min((b["low"] for b in later), default=a["entry"])
        else:
            entered = any(b["low"] < a["entry"] for b in nxt)
            best = a["entry"] - min((b["low"] for b in later), default=a["entry"])
            worst = max((b["high"] for b in later), default=a["entry"]) - a["entry"]
        alerts.append(dict(a, entered=entered, best=best, worst=worst))
    return alerts


def main():
    bars = alert.get_bars_history()
    start, end = bars[0]["start"], bars[-1]["start"]
    weekdays = len({b["start"].astimezone(alert.NY).date() for b in bars
                    if b["start"].astimezone(alert.NY).weekday() < 5})
    out = [f"## Replay of the breakout rules", "",
           f"Data: {start:%b %d} to {end:%b %d, %Y} ({weekdays} weekdays), XAU/USD 30-min candles.", "",
           "### How often it would alert, by setting", "",
           "| Box tightness | Impulse size | Breakout alerts | Per week | Entries triggered |",
           "|---|---|---|---|---|"]
    for tight in (2.0, 2.5, 3.0, 4.0):
        for imp in (1.2, 1.5, 2.0):
            al = replay(bars, tight, imp)
            mark = " (current)" if (tight, imp) == (2.5, 1.5) else ""
            out.append(f"| {tight}x{mark} | {imp}x | {len(al)} | {len(al) / max(weekdays, 1) * 5:.1f} | "
                       f"{sum(a['entered'] for a in al)} |")
    al = replay(bars, 2.5, 1.5)
    morning = [a for a in al if a["at"].hour < 10 or (a["at"].hour == 10 and a["at"].minute == 0)]
    out += ["", f"Of the {len(al)} alerts with current settings, {len(morning)} were 7-10am and "
            f"{len(al) - len(morning)} were after 10am.", ""]
    out += ["", "### Every alert with the current settings", "",
            "| Time (NY) | Side | Entry | Entry hit? | Best move in 2h | Worst move in 2h |",
            "|---|---|---|---|---|---|"]
    for a in al:
        out.append(f"| {a['at']:%a %b %d %-I:%M %p} | {a['side']} | ${a['entry']:,.2f} | "
                   f"{'yes' if a['entered'] else 'no'} | +${a['best']:,.2f} | -${a['worst']:,.2f} |")
    out += raja_replay()
    text = "\n".join(out)
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(text + "\n")


def raja_replay():
    key = os.environ.get("TWELVEDATA_API_KEY")
    if not key:
        return []
    bars = alert._spot_bars(key, "15min", 5000)
    days = sorted({raja.trading_day(b["start"]) for b in bars})[1:]
    found = []
    for d in days:
        from datetime import datetime, time
        cutoff = datetime.combine(d, time(13, 0), tzinfo=raja.NY)
        upto = [b for b in bars if b["start"] < cutoff]
        if not upto or raja.trading_day(upto[-1]["start"]) != d:
            continue
        later = [b for b in bars if raja.trading_day(b["start"]) == d]
        for e in raja.find_entries(upto, cutoff):
            e["result"] = raja.outcome(e, later)
            found.append(e)
    wk = len(days)
    count = lambda r: sum(e["result"] == r for e in found)
    out = ["", "## Replay: Raja strategy (15-min, 6pm-noon NY)", "",
           f"{len(found)} entries over {wk} trading days (about {len(found) / max(wk, 1) * 5:.1f} a week).", "",
           f"Hit take profit 2 ({raja.TP2_PIPS} pips): {count('TP2')}, hit TP1 then stopped: {count('TP1 then stop')}, "
           f"TP1 only by day end: {count('TP1')}, stopped out first: {count('SL')}, "
           f"stop and target in the same 15-min candle (can't tell): {count('unclear')}, neither by day end: {count('open')}.", "",
           "| Time (NY) | Side | Setup | Levels | Entry | Stop | Result |", "|---|---|---|---|---|---|---|"]
    for e in found:
        out.append(f"| {e['at']:%a %b %d %-I:%M %p} | {e['side']} | {e['setup']} | {', '.join(n for n, _ in e['levels'])} | "
                   f"${e['entry']:,.2f} | ${e['stop']:,.2f} | {e['result']} |")
    out += ["", "| Setup | Entries | TP2 | TP1 then stop | SL | Unclear |", "|---|---|---|---|---|---|"]
    for name in ("Break & retest", "Rejection", "Range breakout"):
        g = [e for e in found if e["setup"].startswith(name)]
        c = lambda r: sum(e["result"] == r for e in g)
        out.append(f"| {name} | {len(g)} | {c('TP2')} | {c('TP1 then stop')} | {c('SL')} | {c('unclear')} |")
    return out


if __name__ == "__main__":
    main()
