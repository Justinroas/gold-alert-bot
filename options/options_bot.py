"""Options alert bot (alerts only, never trades).

What it sends to Telegram (its own "Options Alerts" group, separate from the gold group):
1. Morning brief (once each weekday, after 8:45am New York):
   - big events coming up (earnings for your watchlist, Fed meetings, inflation and jobs reports)
   - how much the options market expects each upcoming earnings to move the stock
   - for each stock: are its options CHEAP, FAIR or PRICEY right now?
2. Big move alert (9:45am-4pm): a stock on the watchlist moves a lot from yesterday's close.
   Includes the latest headline and the full math for an example call or put.
3. Big bets alert (9:45am-4pm, every 30 min): someone traded a huge number of one option contract
   (volume many times the open interest), a hint that big money expects a move.

"Cheap or pricey" = implied volatility (how much movement the option price assumes)
vs historical volatility (how much the stock really moved over the last 20 trading days).
"""
import json
import math
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

NY = ZoneInfo("America/New_York")
HERE = Path(__file__).parent
STATE_FILE = Path(os.environ.get("STATE_FILE", ".state/options.json"))
DRY_RUN = os.environ.get("DRY_RUN", "") in ("1", "true")
FORCE = os.environ.get("FORCE", "")  # "brief", "scan" or "all": run now, ignore time and repeats

ETFS = {"SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "XLF", "XLE", "SMH"}
MOVE_PCT_STOCK = float(os.environ.get("MOVE_PCT_STOCK", "3"))  # % move from yesterday's close
MOVE_PCT_ETF = float(os.environ.get("MOVE_PCT_ETF", "1.5"))
CHEAP_BELOW = 0.9    # implied vol / real vol below this = cheap
PRICEY_ABOVE = 1.3   # above this = pricey
EVENT_DAYS = 14      # how far ahead the brief looks for Fed / CPI / jobs
EARNINGS_DAYS = 14   # how far ahead it looks for earnings
UOA_MIN_VOLUME = 2000
UOA_VOL_OI = 5
UOA_MIN_DOLLARS = 1_000_000  # ignore small-money bets
UOA_MIN_DOLLARS_ETF = 5_000_000  # SPY/QQQ trade huge amounts every day
RATE = 0.04

BRIEF_START = time(8, 45)
SCAN_START, SCAN_END = time(9, 45), time(16, 5)

# Decision days. Sources: federalreserve.gov FOMC calendar, bls.gov release schedules.
FOMC = ["2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28",
        "2027-09-15", "2027-10-27", "2027-12-08"]
CPI = ["2026-12-10", "2027-01-13", "2027-02-11", "2027-03-10", "2027-04-13", "2027-05-12",
       "2027-06-10", "2027-07-14", "2027-08-11", "2027-09-14", "2027-10-14", "2027-11-10", "2027-12-10"]
JOBS = ["2026-12-04", "2027-01-08", "2027-02-05", "2027-03-05", "2027-04-02", "2027-05-07",
        "2027-06-04", "2027-07-02", "2027-08-06", "2027-09-03", "2027-10-08", "2027-11-05", "2027-12-03"]
MACRO = ([(d, "🏦 Fed interest rate decision (2pm). The whole market can swing.") for d in FOMC]
         + [(d, "🧾 Inflation report, CPI (8:30am). Big market mover.") for d in CPI]
         + [(d, "👷 Jobs report (8:30am). Big market mover.") for d in JOBS])


# ---------- math (no internet needed) ----------

def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(kind, spot, strike, years, vol, rate=RATE):
    if years <= 0 or vol <= 0:
        return max(0.0, spot - strike) if kind == "call" else max(0.0, strike - spot)
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / (vol * math.sqrt(years))
    d2 = d1 - vol * math.sqrt(years)
    if kind == "call":
        return spot * _ncdf(d1) - strike * math.exp(-rate * years) * _ncdf(d2)
    return strike * math.exp(-rate * years) * _ncdf(-d2) - spot * _ncdf(-d1)


def implied_vol(kind, premium, spot, strike, years):
    """The volatility that makes the option worth `premium` (bisection). None if it can't."""
    if premium <= 0 or years <= 0:
        return None
    lo, hi = 0.01, 5.0
    if not (bs_price(kind, spot, strike, years, lo) <= premium <= bs_price(kind, spot, strike, years, hi)):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs_price(kind, spot, strike, years, mid) < premium:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def real_vol(closes, days=20):
    """Annualized volatility of the last `days` daily moves (what the stock really did)."""
    c = closes[-(days + 1):]
    if len(c) < 3:
        return None
    rets = [math.log(b / a) for a, b in zip(c, c[1:])]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def verdict(iv, hv):
    if not iv or not hv:
        return "❔", "UNKNOWN", None
    ratio = iv / hv
    if ratio < CHEAP_BELOW:
        return "🟢", "CHEAP", ratio
    if ratio > PRICEY_ABOVE:
        return "🔴", "PRICEY", ratio
    return "🟡", "FAIR", ratio


def premium_of(row):
    """Best guess of an option's price: middle of bid/ask, else last trade."""
    bid, ask, last = row.get("bid") or 0, row.get("ask") or 0, row.get("lastPrice") or 0
    if bid > 0 and ask > 0 and ask >= bid:
        return (bid + ask) / 2
    return last


def pick_expiry(expiries, today, lo=21, hi=60, target=30):
    """Expiry about a month out: gives the trade time to work without paying for months."""
    ds = [(datetime.strptime(e, "%Y-%m-%d").date(), e) for e in expiries]
    ok = [(d, e) for d, e in ds if lo <= (d - today).days <= hi]
    pool = ok or [(d, e) for d, e in ds if (d - today).days >= 7] or ds
    return min(pool, key=lambda x: abs((x[0] - today).days - target))[1] if pool else None


def nearest_row(rows, price):
    rows = [r for r in rows if r.get("strike")]
    return min(rows, key=lambda r: abs(r["strike"] - price)) if rows else None


def money(x):
    return f"${x:,.2f}" if abs(x) < 1000 else f"${x:,.0f}"


def nice_date(d):
    return d.strftime("%a %b ") + str(d.day)


def trade_math(ticker, kind, price, strike, premium, expiry, today):
    """Plain-words math for buying ONE contract."""
    exp = datetime.strptime(expiry, "%Y-%m-%d").date()
    days = (exp - today).days
    cost = premium * 100
    if kind == "call":
        be = strike + premium
        need = (be / price - 1) * 100
        target = price * (1.10 if ticker not in ETFS else 1.05)
        value = max(0.0, target - strike) * 100
        dirword, endword = "above", "below"
    else:
        be = strike - premium
        need = (1 - be / price) * 100
        target = price * (0.90 if ticker not in ETFS else 0.95)
        value = max(0.0, strike - target) * 100
        dirword, endword = "below", "above"
    move = abs(target / price - 1) * 100
    profit = value - cost
    icon = "📗" if kind == "call" else "📕"
    bet = "goes UP" if kind == "call" else "goes DOWN"
    lines = [
        f"{icon} Example {kind.upper()} (a bet {ticker} {bet}):",
        f"  {ticker} {money(strike)} {kind}, expires {nice_date(exp)} ({days} days)",
        f"  Price {money(premium)} per share. 1 contract = 100 shares,",
        f"  so 1 contract costs {money(premium)} x 100 = {money(cost)}",
        f"  Breakeven: {ticker} must be {dirword} {money(be)} on {nice_date(exp)}"
        f" ({need:+.1f}% from {money(price)})" if kind == "call" else
        f"  Breakeven: {ticker} must be {dirword} {money(be)} on {nice_date(exp)}"
        f" (-{need:.1f}% from {money(price)})",
        f"  Most you can lose: {money(cost)} (if {ticker} ends {endword} {money(strike)})",
        f"  If {ticker} moves {move:.0f}% to {money(target)} by then: worth about ${value:,.0f},"
        f" {'profit' if profit >= 0 else 'loss'} about ${abs(profit):,.0f}",
    ]
    return "\n".join(lines)


def verdict_line(ticker, iv, hv, earnings_soon=False):
    icon, word, ratio = verdict(iv, hv)
    if ratio is None:
        return f"{icon} {ticker}: no reliable option prices right now"
    extra = ""
    if word == "PRICEY" and earnings_soon:
        extra = " (normal right before earnings)"
    return (f"{icon} {ticker}: {word}. Options assume {iv * 100:.0f}% yearly swings,"
            f" it normally moves {hv * 100:.0f}%{extra}")


VERDICT_HELP = ("🟢 CHEAP = options cost less than how much the stock normally moves. Good time to BUY calls/puts.\n"
                "🟡 FAIR = normal price.\n"
                "🔴 PRICEY = you overpay. The stock must move a lot just to break even.")


def upcoming_macro(today, days=EVENT_DAYS):
    out = []
    for d, text in MACRO:
        dd = datetime.strptime(d, "%Y-%m-%d").date()
        if 0 <= (dd - today).days <= days:
            out.append((dd, text))
    return sorted(out)


def when_word(d, today):
    n = (d - today).days
    return "TODAY" if n == 0 else "TOMORROW" if n == 1 else f"in {n} days"


def move_level(pct, threshold):
    """0 = no alert, 1 = big move, 2 = huge move (double the threshold)."""
    a = abs(pct)
    return 2 if a >= 2 * threshold else 1 if a >= threshold else 0


# ---------- market data (Yahoo Finance via yfinance) ----------

class Market:
    def __init__(self):
        import yfinance as yf
        self.yf = yf
        self._t = {}

    def t(self, sym):
        if sym not in self._t:
            self._t[sym] = self.yf.Ticker(sym)
        return self._t[sym]

    def daily(self, sym):
        df = self.t(sym).history(period="1y", interval="1d", auto_adjust=False)
        df = df.dropna(subset=["Close"])
        return [(ts.date(), float(c)) for ts, c in zip(df.index, df["Close"])]

    def chain(self, sym, expiry):
        ch = self.t(sym).option_chain(expiry)
        return ch.calls.to_dict("records"), ch.puts.to_dict("records")

    def expiries(self, sym):
        return list(self.t(sym).options)

    def earnings_date(self, sym, today):
        try:
            cal = self.t(sym).calendar or {}
            ds = cal.get("Earnings Date") or []
        except Exception:
            return None
        ds = [d if isinstance(d, date) else d.date() for d in ds]
        ds = [d for d in ds if d >= today]
        return min(ds) if ds else None

    def headline(self, sym):
        try:
            items = self.t(sym).news or []
        except Exception:
            return None
        for it in items:
            c = it.get("content", it)
            title = c.get("title")
            if title:
                return title
        return None


def snapshot(mkt, sym, today):
    """Price, real vol, ATM implied vol and an example call and put about a month out."""
    days = mkt.daily(sym)
    closes = [c for _, c in days]
    price = closes[-1]
    # "Normal" movement = blend of the last month and the last year, so one wild week
    # doesn't make every option look cheap.
    hv20, hv1y = real_vol(closes, 20), real_vol(closes, 250)
    hv = (hv20 + hv1y) / 2 if hv20 and hv1y else hv20 or hv1y
    s = dict(sym=sym, price=price, hv=hv, iv=None, days=days, opts={})
    exps = mkt.expiries(sym)
    exp = pick_expiry(exps, today)
    if not exp:
        return s
    calls, puts = mkt.chain(sym, exp)
    years = max((datetime.strptime(exp, "%Y-%m-%d").date() - today).days, 1) / 365
    ivs = []
    for kind, rows in (("call", calls), ("put", puts)):
        r = nearest_row(rows, price)
        if not r:
            continue
        p = premium_of(r)
        iv = implied_vol(kind, p, price, r["strike"], years)
        if iv:
            ivs.append(iv)
        if p > 0:
            s["opts"][kind] = dict(strike=r["strike"], premium=p, expiry=exp)
    s["iv"] = sum(ivs) / len(ivs) if ivs else None
    s["exps"] = exps
    return s


def expected_move(mkt, sym, price, earn, exps):
    """How far the options market expects the stock to move by the first expiry after earnings
    (price of the at-the-money call + put, the 'straddle')."""
    after = [e for e in exps if datetime.strptime(e, "%Y-%m-%d").date() > earn]
    if not after:
        return None
    calls, puts = mkt.chain(sym, after[0])
    c, p = nearest_row(calls, price), nearest_row(puts, price)
    if not c or not p:
        return None
    total = premium_of(c) + premium_of(p)
    return total if total > 0 else None


# ---------- messages ----------

def build_brief(mkt, watchlist, today):
    parts = [f"☀️ OPTIONS MORNING BRIEF, {nice_date(today)}"]
    macro = upcoming_macro(today)
    earn_lines, verdicts, snaps = [], [], {}
    for sym in watchlist:
        try:
            s = snapshot(mkt, sym, today)
        except Exception as e:
            print(f"{sym}: snapshot failed: {e}")
            continue
        snaps[sym] = s
        earn = None if sym in ETFS else mkt.earnings_date(sym, today)
        soon = earn is not None and (earn - today).days <= EARNINGS_DAYS
        if soon:
            line = f"📣 {sym} earnings {nice_date(earn)} ({when_word(earn, today)})"
            try:
                em = expected_move(mkt, sym, s["price"], earn, s.get("exps", []))
            except Exception:
                em = None
            if em:
                line += (f"\n    Options bet on a move of about ±{em / s['price'] * 100:.1f}%"
                         f" (±{money(em)}) from {money(s['price'])}")
            earn_lines.append((earn, line))
        verdicts.append(verdict_line(sym, s["iv"], s["hv"], soon))
    parts.append("📅 BIG EVENTS COMING UP")
    ev = [f"• {nice_date(d)} ({when_word(d, today)}): {t}" for d, t in macro]
    ev += [l for _, l in sorted(earn_lines)]
    parts.append("\n".join(ev) if ev else "Nothing big on the calendar. Quiet weeks make options cheaper.")
    if earn_lines:
        parts.append("💡 Earnings tip: options get PRICEY before earnings and drop in value right after"
                     " (the 'IV crush'). Buying calls/puts the day before earnings only pays if the move"
                     " is BIGGER than the ± number above.")
    parts.append("💲 CHEAP OR PRICEY?\n" + "\n".join(verdicts) + "\n\n" + VERDICT_HELP)
    cheapest = [s for s in snaps.values() if verdict(s["iv"], s["hv"])[1] == "CHEAP" and s["opts"].get("call")]
    if cheapest:
        s = min(cheapest, key=lambda s: s["iv"] / s["hv"])
        o = s["opts"]["call"]
        parts.append(f"🧮 THE MATH, cheapest options today: {s['sym']}\n"
                     + trade_math(s["sym"], "call", s["price"], o["strike"], o["premium"], o["expiry"], today))
    parts.append("Alerts only, not advice. Never risk money you can't lose.")
    return "\n\n".join(parts)


def build_move_alert(mkt, s, pct, level, today):
    sym, price = s["sym"], s["price"]
    up = pct > 0
    head = "🚀" if up else "🩸"
    size = "HUGE" if level == 2 else "BIG"
    prev = s["days"][-2][1]
    parts = [f"{head} {size} MOVE: {sym} {'UP' if up else 'DOWN'} {pct:+.1f}% today",
             f"Now {money(price)} (yesterday closed at {money(prev)})"]
    h = mkt.headline(sym)
    if h:
        parts.append(f"📰 Latest news: {h}")
    parts.append(verdict_line(sym, s["iv"], s["hv"]))
    kind = "call" if up else "put"
    o = s["opts"].get(kind)
    if o:
        parts.append("If you think the move keeps going:\n"
                     + trade_math(sym, kind, price, o["strike"], o["premium"], o["expiry"], today))
    parts.append("⚠️ Chasing a big move is risky. Stocks often snap back the same day.")
    return "\n\n".join(parts)


def find_big_bets(mkt, sym, price, today, exps, n_exp=2):
    out = []
    later = [e for e in exps if (datetime.strptime(e, "%Y-%m-%d").date() - today).days >= 2]
    for exp in later[:n_exp]:
        calls, puts = mkt.chain(sym, exp)
        for kind, rows in (("call", calls), ("put", puts)):
            for r in rows:
                vol, oi = r.get("volume") or 0, r.get("openInterest") or 0
                if vol != vol or oi != oi:  # NaN
                    continue
                prem = premium_of(r)
                if (vol >= UOA_MIN_VOLUME and vol >= UOA_VOL_OI * max(oi, 1)
                        and prem * 100 * vol >= (UOA_MIN_DOLLARS_ETF if sym in ETFS else UOA_MIN_DOLLARS)):
                    out.append(dict(sym=sym, kind=kind, strike=r["strike"], expiry=exp,
                                    volume=int(vol), oi=int(oi), premium=prem,
                                    dollars=prem * 100 * vol))
    return out


def build_big_bet_alert(b, price):
    exp = datetime.strptime(b["expiry"], "%Y-%m-%d").date()
    bet = "UP 📈" if b["kind"] == "call" else "DOWN 📉"
    return (f"🐳 BIG BET: {b['sym']} {money(b['strike'])} {b['kind']}s expiring {nice_date(exp)}\n"
            f"{b['volume']:,} contracts traded today vs {b['oi']:,} that existed before"
            f" (about {money(b['dollars'])} spent). Big money may expect {b['sym']} to go {bet}.\n"
            f"Stock now {money(price)}, option about {money(b['premium'])} ({money(b['premium'] * 100)} per contract).\n"
            "Could also be a hedge or a seller, so treat it as a heads-up, not a signal.")


# ---------- Telegram ----------

def TOKEN():
    """Its own options bot if set up, else the gold bot account (just the messenger)."""
    return os.environ.get("OPTIONS_TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")


def tg(method, **params):
    r = requests.post(f"https://api.telegram.org/bot{TOKEN()}/{method}",
                      data=params, timeout=30)
    r.raise_for_status()
    return r.json()


def find_chat_id(state):
    """The chat where someone sent /options: the "Options Alerts" group (or a private chat).
    Never the gold group, so the two bots' alerts stay separate."""
    cid = os.environ.get("OPTIONS_TELEGRAM_CHAT_ID") or state.get("chat_id")
    if cid:
        return cid
    if not TOKEN():
        return None
    gold = os.environ.get("GOLD_CHAT_ID", "")
    for u in reversed(tg("getUpdates").get("result", [])):
        m = u.get("message") or {}
        chat = str(m.get("chat", {}).get("id", ""))
        if chat and chat != gold and (m.get("text") or "").strip().lower().startswith("/options"):
            state["chat_id"] = chat
            state["new_chat"] = True
            return chat
    return None


def send(text, chat_id, state=None):
    print(text + "\n" + "-" * 40)
    if DRY_RUN or not chat_id:
        return chat_id
    while text:
        chunk, text = text[:4000], text[4000:]
        if text:  # split at a line break
            cut = chunk.rfind("\n")
            if cut > 0:
                chunk, text = chunk[:cut], chunk[cut + 1:] + text
        r = requests.post(f"https://api.telegram.org/bot{TOKEN()}/sendMessage",
                          data=dict(chat_id=chat_id, text=chunk, disable_web_page_preview="true"), timeout=30)
        new_id = (r.json().get("parameters") or {}).get("migrate_to_chat_id") if r.status_code == 400 else None
        if new_id:  # Telegram upgraded the group to a supergroup: new id, send again
            chat_id = str(new_id)
            if state is not None:
                state["chat_id"] = chat_id
            r = requests.post(f"https://api.telegram.org/bot{TOKEN()}/sendMessage",
                              data=dict(chat_id=chat_id, text=chunk, disable_web_page_preview="true"), timeout=30)
        r.raise_for_status()
    return chat_id


# ---------- main ----------

def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def load_watchlist():
    raw = os.environ.get("WATCHLIST") or (HERE / "watchlist.txt").read_text()
    out = []
    for line in raw.replace(",", "\n").splitlines():
        sym = line.split("#")[0].strip().upper()
        if sym and sym not in out:
            out.append(sym)
    return out


def main():
    now = datetime.now(NY)
    today = now.date()
    state = load_state()
    chat_id = find_chat_id(state)
    if not chat_id and not DRY_RUN:
        print("No chat yet: add the bot to the Options Alerts group and send /options there")
        save_state(state)
        return
    if state.pop("new_chat", False):
        send("✅ Options alerts are ON for this chat.\nEvery weekday: a morning brief at 8:45am New York,"
             " plus alerts for big moves and big bets on the watchlist.\nAlerts only, not advice.", chat_id, state)
    weekday = today.weekday() < 5
    watch = load_watchlist()
    mkt = Market()
    sent = state.setdefault("sent", {})
    day = today.isoformat()
    for k in [k for k in sent if not k.startswith(day)]:
        del sent[k]

    if FORCE in ("brief", "all") or (weekday and now.time() >= BRIEF_START and state.get("brief") != day
                                     and now.time() < SCAN_END):
        chat_id = send(build_brief(mkt, watch, today), chat_id, state)
        state["brief"] = day
        save_state(state)

    if FORCE in ("scan", "all") or (weekday and SCAN_START <= now.time() <= SCAN_END):
        do_uoa = FORCE or now.timestamp() - state.get("uoa_at", 0) >= 29 * 60
        for sym in watch:
            try:
                days = mkt.daily(sym)
                if len(days) < 3 or (days[-1][0] != today and not FORCE):
                    continue  # no trading yet today
                pct = (days[-1][1] / days[-2][1] - 1) * 100
                lvl = move_level(pct, MOVE_PCT_ETF if sym in ETFS else MOVE_PCT_STOCK)
                key = f"{day}:move:{sym}:{'up' if pct > 0 else 'down'}"
                print(f"{sym}: {pct:+.2f}% level {lvl}")
                if lvl and (sent.get(key, 0) < lvl or FORCE):
                    s = snapshot(mkt, sym, today)
                    chat_id = send(build_move_alert(mkt, s, pct, lvl, today), chat_id, state)
                    sent[key] = lvl
                if do_uoa:
                    bets = find_big_bets(mkt, sym, days[-1][1], today, mkt.expiries(sym))
                    for b in sorted(bets, key=lambda b: -b["dollars"])[:1]:
                        k = f"{day}:bet:{sym}:{b['kind']}:{b['strike']}:{b['expiry']}"
                        if k not in sent or FORCE:
                            chat_id = send(build_big_bet_alert(b, days[-1][1]), chat_id, state)
                            sent[k] = 1
            except Exception as e:
                print(f"{sym}: scan failed: {e}")
        if do_uoa:
            state["uoa_at"] = now.timestamp()
    save_state(state)


if __name__ == "__main__":
    main()
