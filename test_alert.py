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
    bars, now = make(2012, 2013, 2003, last_close_ny=(16, 30))
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


def _p(side="BUY", entry=2013.0, mins_ago=10):
    now = datetime(2026, 11, 9, 8, 40, tzinfo=NY)
    return dict(side=side, entry=entry, since=(now - timedelta(minutes=mins_ago)).isoformat()), now

def _b5(now, highs_lows):
    return [dict(start=now - timedelta(minutes=5 * (len(highs_lows) - i)), high=h, low=l, close=h, open=l, volume=0)
            for i, (h, l) in enumerate(highs_lows)]

def test_entry_triggers_on_break_of_high():
    p, now = _p()
    r, price = alert.check_entry(p, _b5(now, [(2012, 2010), (2014.5, 2011)]), now)
    assert r == "triggered" and price == 2014.5
    assert "ENTRY TRIGGERED" in alert.format_entry(p, price)

def test_entry_waits_then_expires():
    p, now = _p(mins_ago=10)
    assert alert.check_entry(p, _b5(now, [(2012, 2010)]), now) == (None, None)
    p, now = _p(mins_ago=70)
    assert alert.check_entry(p, _b5(now, [(2012, 2010)]), now)[0] == "expired"

def test_sell_entry_ignores_bars_before_breakout():
    p, now = _p(side="SELL", entry=1998.0, mins_ago=10)
    bars = _b5(now, [(2000, 1990), (2001, 1999), (2000, 1999)])  # the 1990 low is before the breakout
    assert alert.check_entry(p, bars, now) == (None, None)

def test_main_alerts_once_then_entry(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bars, now = make(2012, 2013, 2003)
    sent = []
    monkeypatch.setattr(alert, "get_bars", lambda: bars)
    monkeypatch.setattr(alert, "send_telegram", sent.append)
    monkeypatch.setattr(alert, "get_bars_5min", lambda: [])
    class D(datetime):
        @classmethod
        def now(cls, tz=None):
            return D.t
    D.t = now
    monkeypatch.setattr(alert, "datetime", D)
    alert.main(); alert.main()
    assert len(sent) == 1 and "BREAKOUT" in sent[0]
    D.t = now + timedelta(minutes=5)
    monkeypatch.setattr(alert, "get_bars_5min", lambda: [dict(start=now, high=2020.0, low=2012.0)])
    alert.main(); alert.main()
    assert len(sent) == 2 and "ENTRY TRIGGERED" in sent[1]


# ---- Raja Banks break & retest ----
import raja
from datetime import datetime as _dt, timedelta as _td
from zoneinfo import ZoneInfo as _Z

_NY = _Z("America/New_York")


def _raja_day(break_bars):
    """Yesterday's range 4000-4010, Asia 4002-4008, London 4003-4009, then today's NY bars."""
    bars = []
    t = _dt(2026, 11, 9, 18, 0, tzinfo=_NY)  # Tue trading day for Mon 6pm onwards = Nov 10
    # previous trading day (Nov 9 6pm-ish back): bars on Nov 8 18:00..Nov 9 17:00
    p = _dt(2026, 11, 8, 18, 0, tzinfo=_NY)
    for i in range(92):
        bars.append(dict(start=p + _td(minutes=15 * i), open=4005, high=4010 if i == 10 else 4006,
                         low=4000 if i == 20 else 4004, close=4005, volume=0))
    for i in range(52):  # 6pm-7am today
        ts = t + _td(minutes=15 * i)
        hi = 4008 if ts.astimezone(_NY).hour < 3 or ts.astimezone(_NY).hour >= 18 else 4009
        bars.append(dict(start=ts, open=4005, high=hi if i in (5, 40) else 4006,
                         low=4002 if i == 6 else (4003 if i == 41 else 4004), close=4005, volume=0))
    t7 = _dt(2026, 11, 10, 7, 0, tzinfo=_NY)
    for i, (o, h, l, c) in enumerate(break_bars):
        bars.append(dict(start=t7 + _td(minutes=15 * i), open=o, high=h, low=l, close=c, volume=0))
    return bars


def test_raja_buy_break_and_retest():
    bars = _raja_day([(4005, 4007, 4004, 4006),       # 7:00 quiet
                      (4006, 4014, 4006, 4013),       # 7:15 breaks yesterday's high 4010
                      (4013, 4013.5, 4010.5, 4012)])  # 7:30 retests 4010 and holds
    now = _dt(2026, 11, 10, 7, 46, tzinfo=_NY)
    es = raja.find_entries(bars, now)
    assert len(es) == 1
    e = es[0]
    assert e["side"] == "BUY" and e["entry"] == 4012
    assert any(n == "yesterday's high" for n, _ in e["levels"])
    assert abs(e["tp1"] - 4015.5) < 1e-6 and abs(e["tp2"] - 4017) < 1e-6
    assert abs(e["stop"] - 4010.0) < 1e-6  # retest low 4010.5 minus $0.50
    assert "Raja strategy" in raja.format_raja(e) and "Stop loss" in raja.format_raja(e)


def test_raja_failed_break_no_entry():
    bars = _raja_day([(4005, 4007, 4004, 4006),
                      (4006, 4014, 4006, 4013),       # break
                      (4013, 4013, 4007, 4007.5)])    # closes back below all levels: failed
    now = _dt(2026, 11, 10, 7, 46, tzinfo=_NY)
    assert raja.find_entries(bars, now) == []


def test_raja_outside_hours():
    bars = _raja_day([(4005, 4007, 4004, 4006)] * 17 +   # 7:00-11:00 quiet
                     [(4006, 4014, 4006, 4013), (4013, 4013.5, 4010.5, 4012)])  # 11:15 break
    now = _dt(2026, 11, 10, 11, 50, tzinfo=_NY)
    assert raja.find_entries(bars, now) == []
