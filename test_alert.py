from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import alert

NY = ZoneInfo("America/New_York")

def make(last_close, last_high, last_low, last_vol=100, box=(2000, 2004), last_close_ny=(8, 30)):
    end = datetime(2026, 11, 9, *last_close_ny, tzinfo=NY)
    n = alert.AVG_BARS + alert.BOX_BARS + 5
    bars = []
    for i in range(n):
        start = end - alert.BAR * (n - i)
        lo, hi = box
        bars.append(dict(start=start, open=lo + 1, high=hi, low=lo, close=lo + 2, volume=100))
    bars[-1].update(close=last_close, high=last_high, low=last_low, volume=last_vol)
    return bars, end + timedelta(minutes=1)

def test_buy_breakout_big_candle():
    bars, now = make(2012, 2013, 2003)
    a = alert.check(bars, now)
    assert a and a["side"] == "BUY" and a["entry"] == 2013
    assert "BUY" in alert.format_alert(a)

def test_sell_breakout_on_volume():
    bars, now = make(1999, 2001, 1998, last_vol=300)
    a = alert.check(bars, now)
    assert a and a["side"] == "SELL" and "volume" in a["why"]

def test_weak_break_ignored():
    bars, now = make(2005, 2005.5, 2002)  # small candle, normal volume
    assert alert.check(bars, now) is None

def test_outside_hours_ignored():
    bars, now = make(2012, 2013, 2003, last_close_ny=(11, 0))
    assert alert.check(bars, now) is None

def test_no_consolidation_ignored():
    bars, now = make(2012, 2013, 2003)
    bars[-3].update(high=2030)  # box too wide
    assert alert.check(bars, now) is None

def test_unfinished_candle_not_used():
    bars, now = make(2012, 2013, 2003)
    assert alert.check(bars, now - timedelta(minutes=2)) is None

def test_spot_prices_get_futures_volume(monkeypatch):
    from datetime import timezone
    t = datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("TWELVEDATA_API_KEY", "x")
    monkeypatch.setattr(alert, "_yahoo_bars", lambda tk: [dict(start=t.astimezone(NY), volume=555.0)])
    monkeypatch.setattr(alert, "_spot_bars", lambda k: [dict(start=t, close=4000.0, volume=0.0)])
    assert alert.get_bars()[0]["volume"] == 555.0
