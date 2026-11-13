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


def test_prob_profit():
    # breakeven at today's price, tiny drift: about a coin flip
    assert 0.4 < ob.prob_profit("call", 100, 100, 30 / 365, 0.4) < 0.55
    # far away breakeven: small chance
    assert ob.prob_profit("call", 100, 130, 30 / 365, 0.4) < 0.05
    assert ob.prob_profit("put", 100, 70, 30 / 365, 0.4) < 0.05


def test_classify_setup():
    # up 5%, closing near the high -> call
    assert ob.classify_setup(5, 110, 100, 109)[0] == "call"
    # up 5% but back near the low -> wait
    k, label, _ = ob.classify_setup(5, 110, 100, 102)
    assert k is None and "fading" in label
    # down, holding near the low -> put
    assert ob.classify_setup(-5, 100, 90, 91)[0] == "put"
    # middle -> wait
    assert ob.classify_setup(-5, 100, 90, 95)[0] is None


class FakeMkt:
    def expiries(self, sym):
        return ["2026-11-13", "2026-11-27", "2026-12-18", "2027-03-19"]

    def chain(self, sym, exp):
        years = (date.fromisoformat(exp) - date(2026, 11, 12)).days / 365
        rows = []
        for k in range(90, 131, 5):
            p = round(ob.bs_price("call", 100, k, years, 0.5), 2)
            rows.append(dict(strike=k, bid=p - 0.02, ask=p + 0.02, lastPrice=p))
        return rows, []


def test_budget_pick_fits_budget_and_window():
    pick = ob.budget_pick(FakeMkt(), "XYZ", "call", 100, date(2026, 11, 12), budget=300)
    assert pick and pick["premium"] * 100 <= 300
    assert pick["expiry"] in ("2026-11-27", "2026-12-18")  # 7-45 days only
    t = ob.budget_text("XYZ", "call", 100, pick, date(2026, 11, 12), budget=300)
    assert "YOUR $300 PICK" in t and "Chance it pays off" in t
    assert "PICK: none" in ob.budget_text("XYZ", "call", 100, None, date(2026, 11, 12))


def test_risk_tags():
    pick = dict(strike=720, premium=0.83, expiry="2026-11-20", prob=0.03)
    t = ob.budget_text("AMD", "call", 581, pick, date(2026, 11, 12))
    assert "LOTTERY TICKET" in t and "$720.83" in t and "about 3%" in t
    assert ob.risk_tag(0.4)[0] == "🟢 CLEAR TRADE"
    assert ob.risk_tag(0.2)[0] == "🟡 RISKY"
    assert ob.risk_tag(0.1)[0] == "🔴 LOTTERY TICKET"


def test_sample_alerts_cover_each_risk_level():
    texts = ob.sample_alerts(date(2026, 11, 12))
    assert len(texts) == 6 and "· SPY" in texts[3] and "RSI-2" in texts[4] and "CLOSE POSITION" in texts[5]
    assert "Clear trade" in texts[0] and "Risky" in texts[1] and "Lottery ticket" in texts[2]
    assert all(t.startswith("🧪 TEST") for t in texts)


def _bars(day, rows):
    from datetime import datetime, time, timedelta
    t0 = datetime.combine(day, time(9, 30), tzinfo=ob.NY)
    return [dict(start=t0 + timedelta(minutes=5 * i), high=h, low=l, close=c) for i, (h, l, c) in enumerate(rows)]


def test_orb_signal_call_with_vix_agreeing():
    from datetime import datetime, time
    d = date(2026, 11, 12)
    spy = _bars(d, [(101, 99, 100), (100.5, 99.5, 100), (100.8, 99.2, 100.5),  # range 99-101
                    (100.9, 100.2, 100.7), (101.6, 100.8, 101.4), (102, 101, 101.8)])
    vix = _bars(d, [(20, 19, 19.5)] * 6)
    sig = ob.orb_signal(spy, vix, 20.0, datetime.combine(d, time(10, 30), tzinfo=ob.NY))
    assert sig["side"] == "call" and sig["price"] == 101.4 and sig["agree"]
    assert sig["stop"] == 100 and sig["target"] == 103.4
    # VIX up while SPY breaks up: disagrees
    assert not ob.orb_signal(spy, vix, 19.0, datetime.combine(d, time(10, 30), tzinfo=ob.NY))["agree"]
    # candle not finished yet: no signal
    assert ob.orb_signal(spy, vix, 20.0, datetime.combine(d, time(9, 52), tzinfo=ob.NY)) is None
    text = ob.build_orb_alert(sig, None, d)
    assert text.startswith("📗 BUY CALL · SPY")


def test_dip_signal_and_exit():
    up = [100 + i * 0.2 for i in range(220)]           # long uptrend
    dumped = up + [up[-1] * 0.98, up[-1] * 0.96]       # two hard down days
    assert ob.dip_signal(dumped, 24) is not None       # scared market -> signal
    assert ob.dip_signal(dumped, 15) is None           # calm market -> no signal
    assert ob.dip_signal(up, 24) is None               # no dip -> no signal
    crash = [200 - i * 0.3 for i in range(220)] + [130, 125]
    assert ob.dip_signal(crash, 30) is None            # below its 200-day avg -> no signal
    assert ob.dip_exit(dumped + [up[-1] * 1.01], 1) == "bounce"
    assert ob.dip_exit(dumped + [up[-1] * 0.95], 1) is None
    assert ob.dip_exit(dumped + [up[-1] * 0.95], 10) == "time"


def test_news_parse_pick_and_format():
    new_style = {"content": {"title": "Stocks rally on Fed hopes", "pubDate": "2026-11-12T15:00:00Z",
                             "provider": {"displayName": "Reuters"}, "canonicalUrl": {"url": "https://x.test/a"}}}
    old_style = {"title": "Nvidia slides as CEO Huang exits", "publisher": "CNBC", "link": "https://x.test/b",
                 "providerPublishTime": 1794490000}
    junk = {"title": "3 IRA Moves to Make Now", "publisher": "Fool", "providerPublishTime": 1794496000}
    offtopic = {"title": "Pimco CEO warns on AI borrowing", "publisher": "Fool", "providerPublishTime": 1794496000}
    a, b = ob.parse_news(new_style, "SPY"), ob.parse_news(old_style, "NVDA")
    j, o = ob.parse_news(junk, "NVDA"), ob.parse_news(offtopic, "AMZN")
    assert a["source"] == "Reuters" and a["link"] == "https://x.test/a" and a["ts"]
    assert b["source"] == "CNBC" and b["ts"] == 1794490000
    now = a["ts"] + 600
    picked = ob.pick_news([a, b, dict(a), j, o], set(), now)
    assert [n["title"] for n in picked] == ["Stocks rally on Fed hopes", "Nvidia slides as CEO Huang exits"]
    assert ob.pick_news([a], {"stocks rally on fed hopes"}, now) == []   # already sent today
    assert ob.pick_news([a], set(), a["ts"] + 13 * 3600) == []           # too old
    text = ob.build_news(b, -5.9)
    assert text.startswith("📰 MARKET NEWS · NVDA") and "Why it matters: NVDA is -5.9% today. A leadership" in text
    assert "Fed and interest-rate news" in ob.build_news(a, 0.4)


def test_sec_filings_and_ipos():
    sub = {"filings": {"recent": {
        "form": ["8-K", "4", "8-K"], "accessionNumber": ["0001-26-000001", "x", "0001-26-000002"],
        "items": ["5.02,9.01", "", "5.07"], "acceptanceDateTime": ["2026-11-12T13:05:00.000Z", "", "2026-11-11T20:00:00.000Z"],
        "primaryDocument": ["a.htm", "", "b.htm"]}}}
    fs = ob.parse_8k(sub, "NVDA", 1045810)
    assert [f["acc"] for f in fs] == ["0001-26-000001", "0001-26-000002"]
    text = ob.build_filing(fs[0], -5.7)
    assert text.startswith("📄 COMPANY FILING · NVDA") and "leadership change" in text and "-5.7%" in text
    assert "/1045810/000126000001/a.htm" in text
    assert ob.build_filing(fs[1]) is None  # shareholder vote results: not worth an alert
    xml = ('<feed><entry><title>S-1 - Anthropic PBC (0001234567) (Filer)</title><link href="https://sec.test/1"/></entry>'
           '<entry><title>S-1/A - Tiny Shell Co (0007654321) (Filer)</title><link href="https://sec.test/2"/></entry></feed>')
    feed = ob.parse_ipo_feed(xml)
    assert [f["name"] for f in feed] == ["Anthropic PBC", "Tiny Shell Co"]
    assert ob.famous("Anthropic PBC") == "anthropic" and ob.famous("Tiny Shell Co") is None
    assert ob.famous("Scale AI, Inc.") == "scale ai" and ob.famous("Rampart Mining") is None
    assert ob.build_ipo_filing(feed[0]).startswith("🚀 IPO NEWS · Anthropic PBC")
    import pandas as pd
    df = pd.DataFrame([dict(Symbol="BIG", Company="Big Co", Date="2026-11-20", **{"Price From": 20, "Price To": 24, "Shares": 50e6}),
                       dict(Symbol="SML", Company="Small Co", Date="2026-11-20", **{"Price From": 4, "Price To": 5, "Shares": 1e6})])
    rows = ob.ipo_rows(df, date(2026, 11, 12))
    assert [r["sym"] for r in rows] == ["BIG"]
    assert "raising about $1.2B" in ob.build_ipo_upcoming(rows[0]) and "Nov 20" in ob.build_ipo_upcoming(rows[0])


def test_company_numbers():
    q = lambda y, m, d, v: dict(start=(date(y, m, d) - __import__("datetime").timedelta(days=91)).isoformat(),
                                end=date(y, m, d).isoformat(), val=v)
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [q(2025, 9, 30, 1382e6), q(2025, 12, 31, 2040e6), q(2026, 3, 31, 4731e6),
                                       q(2026, 6, 30, 11555e6), q(2026, 9, 30, 16956e6),
                                       dict(start="2025-01-01", end="2025-12-31", val=4585e6)]}},
        "NetIncomeLoss": {"units": {"USD": [q(2026, 9, 30, -23291e6), dict(start="2025-01-01", end="2025-12-31", val=-41965e6)]}}}}}
    line = ob.facts_numbers(facts)
    assert line.startswith("📊 Last quarter (Sep 2026): sales $17.0B (+1127% vs a year ago), loss $23.3B")
    assert "$2.0B → $4.7B → $11.6B → $17.0B" in line and "Full year 2025: sales $4.6B, loss $42.0B" in line
    assert ob.fmt_b(-567e6) == "-$567M"
    assert "📊 Last quarter" in ob.build_news(dict(sym="NVDA", title="t", source="", link=""), None, "📊 Last quarter x")
