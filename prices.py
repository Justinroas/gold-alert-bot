"""Print today's XAU/USD 5-min candles (New York time) so we can check what happened after an alert."""
import os
from zoneinfo import ZoneInfo

import alert

NY = ZoneInfo("America/New_York")
bars = alert._spot_bars(os.environ["TWELVEDATA_API_KEY"], "5min", int(os.environ.get("BARS", "200")))
for b in bars:
    print(f"{b['start'].astimezone(NY):%a %H:%M}  O {b['open']:.2f}  H {b['high']:.2f}  L {b['low']:.2f}  C {b['close']:.2f}")
