"""Send example alerts (clearly marked as tests) so the group can see what real ones look like."""
from datetime import datetime

import os

import alert
import patterns

TAG = "🧪 TEST ALERT (example only, not a real signal)\n\n"


def sensei():
    """Example Sensei EUR/USD alert built around the live euro price."""
    price = alert._spot_bars(os.environ["TWELVEDATA_API_KEY"], "15min", 2, patterns.SYMBOL)[-1]["close"]
    bottom, neck = price - 0.0012, price - 0.0003
    e = patterns._entry("BUY", price, bottom, datetime.now(alert.NY), "Double bottom",
                        f"Two bottoms at about {patterns.px(bottom)}, then a candle closed above the "
                        f"resistance {patterns.px(neck)}.")
    msg = TAG + patterns.format_pattern(e)
    alert.send_telegram(msg)
    print(msg)


def main():
    if os.environ.get("SAMPLE") == "sensei":
        return sensei()
    a = dict(side="BUY", close=4124.60, top=4118.90, bottom=4101.20, entry=4127.35,
             why="candle 2.1x normal size, volume 1.8x normal",
             at=datetime(2026, 11, 10, 8, 30, tzinfo=alert.NY))
    pending = dict(side="BUY", entry=4127.35)
    for msg in (alert.format_alert(a), alert.format_entry(pending, 4128.10)):
        alert.send_telegram(TAG + msg)
        print(TAG + msg)


if __name__ == "__main__":
    main()
