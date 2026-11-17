"""Replay the Sensei strategy on EUR/USD 15-min history (all hours), to pick setups, hours and pips."""
import os
from collections import defaultdict
from datetime import time

import alert
import patterns
import raja


def run(bars, tp1, tp2, cap):
    patterns.TP1_PIPS, patterns.TP2_PIPS, patterns.MAX_STOP_PIPS = tp1, tp2, cap
    found = []
    for n in range(30, len(bars) + 1):
        for e in patterns.check_last(bars[max(0, n - patterns.LOOKBACK):n]):
            e["result"] = raja.outcome(e, bars[n:n + 32])
            e["stop_pips"] = abs(e["entry"] - e["stop"]) / patterns.PIP
            e["pips"] = {"TP2": tp2, "TP1 then stop": tp1 / 2 - e["stop_pips"] / 2,
                         "SL": -e["stop_pips"]}.get(e["result"], 0)
            found.append(e)
    return found


def main():
    patterns.START, patterns.END = time(0, 0), time(23, 59)
    bars = alert._spot_bars(os.environ["TWELVEDATA_API_KEY"], "15min", 5000, patterns.SYMBOL)
    out = [f"## Sensei on {patterns.SYMBOL}: {bars[0]['start']:%b %d} to {bars[-1]['start']:%b %d, %Y}", ""]
    for tp1, tp2, cap in ((10, 20, 15), (15, 30, 20), (20, 40, 25), (25, 50, 30)):
        found = run(bars, tp1, tp2, cap)
        out += [f"### TP {tp1}/{tp2} pips, max stop {cap}", "",
                "| Group | Trades | TP2 | TP1 then stop | SL | Unclear/open | Net pips |", "|---|---|---|---|---|---|---|"]
        groups = defaultdict(list)
        for e in found:
            groups[e["setup"]].append(e)
            groups["session: " + raja.session_name(e["at"])].append(e)
            groups[f"{e['setup']} / {raja.session_name(e['at'])}"].append(e)
            h = e["at"].astimezone(raja.NY).hour
            groups[f"hour {h:02d} NY"].append(e)
        groups["ALL"] = found
        for name in sorted(groups):
            g = groups[name]
            c = lambda r: sum(e["result"] == r for e in g)
            out.append(f"| {name} | {len(g)} | {c('TP2')} | {c('TP1 then stop')} | {c('SL')} | "
                       f"{c('unclear') + c('open') + c('TP1')} | {sum(e['pips'] for e in g):+.0f} |")
        out.append("")
    text = "\n".join(out)
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        open(path, "a").write(text + "\n")


if __name__ == "__main__":
    main()
