"""Send example alerts (clearly marked as tests) so the group can see what real ones look like."""
from datetime import datetime

import alert

TAG = "🧪 TEST ALERT (example only, not a real signal)\n\n"


def main():
    a = dict(side="BUY", close=4124.60, top=4118.90, bottom=4101.20, entry=4127.35,
             why="candle 2.1x normal size, volume 1.8x normal",
             at=datetime(2026, 11, 10, 8, 30, tzinfo=alert.NY))
    pending = dict(side="BUY", entry=4127.35)
    for msg in (alert.format_alert(a), alert.format_entry(pending, 4128.10)):
        alert.send_telegram(TAG + msg)
        print(TAG + msg)


if __name__ == "__main__":
    main()
