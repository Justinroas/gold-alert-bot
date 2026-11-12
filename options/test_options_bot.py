from datetime import date

import options_bot as ob


def test_iv_roundtrip():
    p = ob.bs_price("call", 100, 100, 30 / 365, 0.4)
    assert abs(ob.implied_vol("call", p, 100, 100, 30 / 365) - 0.4) < 1e-4
    p = ob.bs_price("put", 100, 95, 60 / 365, 0.25)
    assert abs(ob.implied_vol("put", p, 100, 95, 60 / 365) - 0.25) < 1e-4
    assert ob.implied_vol("call", 0, 100, 100, 0.1) is None


def test_real_vol_flat_and_moving():
    assert ob.real_vol([100] * 30) == 0
    closes = [100 * (1.02 if i % 2 else 0.98) for i in range(30)]
    assert ob.real_vol(closes) > 0.5


def test_verdict():
    assert ob.verdict(0.3, 0.4)[1] == "CHEAP"
    assert ob.verdict(0.4, 0.4)[1] == "FAIR"
    assert ob.verdict(0.8, 0.4)[1] == "PRICEY"
    assert ob.verdict(None, 0.4)[1] == "UNKNOWN"


def test_premium_of():
    assert ob.premium_of(dict(bid=1.0, ask=1.2, lastPrice=5)) == 1.1
    assert ob.premium_of(dict(bid=0, ask=0, lastPrice=0.9)) == 0.9


def test_pick_expiry():
    today = date(2026, 11, 12)
    exps = ["2026-11-13", "2026-11-20", "2026-12-11", "2026-12-18", "2027-01-15", "2027-06-18"]
    assert ob.pick_expiry(exps, today) == "2026-12-11"
    assert ob.pick_expiry(["2026-11-13", "2026-11-27"], today) == "2026-11-27"


def test_trade_math_call():
    t = ob.trade_math("NVDA", "call", 190, 190, 6.20, "2026-12-18", date(2026, 11, 12))
    assert "$6.20 x 100 = $620.00" in t
    assert "above $196.20" in t
    assert "Most you can lose: $620.00" in t
    assert "36 days" in t
    # +10% -> 209: worth (209-190)*100 = 1900, profit 1280
    assert "$1,900" in t and "profit about $1,280" in t


def test_trade_math_put():
    t = ob.trade_math("SPY", "put", 600, 600, 10, "2026-12-18", date(2026, 11, 12))
    assert "below $590.00" in t and "-1.7%" in t
    # -5% -> 570: worth 3000, profit 2000
    assert "$3,000" in t and "profit about $2,000" in t


def test_macro_and_levels():
    ev = ob.upcoming_macro(date(2026, 12, 3))
    assert [d.isoformat() for d, _ in ev] == ["2026-12-04", "2026-12-09", "2026-12-10"]
    assert ob.move_level(3.5, 3) == 1 and ob.move_level(-6.2, 3) == 2 and ob.move_level(1, 3) == 0
    assert ob.when_word(date(2026, 12, 4), date(2026, 12, 3)) == "TOMORROW"


def test_watchlist(monkeypatch):
    monkeypatch.setenv("WATCHLIST", "spy, nvda\nNVDA # dup")
    assert ob.load_watchlist() == ["SPY", "NVDA"]
    monkeypatch.delenv("WATCHLIST")
    assert "SPY" in ob.load_watchlist()
