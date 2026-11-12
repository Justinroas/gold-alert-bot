"""Options alert bot (alerts only, never trades).

What it sends to Telegram (its own "Options Alerts" group, separate from the gold group):
1. Morning brief (once each weekday, after 8:45am New York):
   - big events coming up (earnings for your watchlist, Fed meetings, inflation and jobs reports)
   - how much the options market expects each upcoming earnings to move the stock
   - for each stock: are its options CHEAP, FAIR or PRICEY right now?
2. Big move alert (9:45am-4pm): a stock on the watchlist moves a lot from yesterday's close.
   Says whether there's a setup (call or put) or not, and picks an option that fits the
   budget (default $100) with its cost, breakeven, chance of paying off and max loss.

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
BUDGET = float(os.environ.get("BUDGET", "100"))  # most you want to spend per trade, in dollars
PICK_MIN_DAYS, PICK_MAX_DAYS = 7, 45  # expiries the budget pick looks at
HOLD_STRONG = 0.6  # price in the top 40% of today's range (bottom 40% for drops) = holding the move
HOLD_WEAK = 0.35   # gave back most of the move = fading
MIN_CHANCE = 0.10  # the brief's example trade needs at least this chance
CLEAR_CHANCE = 0.30  # chance of paying off: 30%+ = clear trade, 15-30% = risky, under 15% = lottery ticket
RISKY_CHANCE = 0.15
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


def trade_math(ticker, kind, price, strike, premium, expiry, today, prob=None, title=None):
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
        title or f"{icon} Example {kind.upper()} (a bet {ticker} {bet}):",
        f"  {ticker} {money(strike)} {kind}, expires {nice_date(exp)} ({days} days)",
        f"  Price {money(premium)} per share. 1 contract = 100 shares,",
        f"  so 1 contract costs {money(premium)} x 100 = {money(cost)}",
        f"  Breakeven: {ticker} must be {dirword} {money(be)} on {nice_date(exp)}"
        f" ({need:+.1f}% from {money(price)})" if kind == "call" else
        f"  Breakeven: {ticker} must be {dirword} {money(be)} on {nice_date(exp)}"
        f" (-{need:.1f}% from {money(price)})",
        *([f"  Chance it pays off by then (options-market math): about {prob * 100:.0f}%"
           ] if prob is not None else []),
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


def prob_profit(kind, spot, breakeven, years, vol, rate=RATE):
    """Chance the stock ends past the breakeven at expiry, using the volatility the option
    price itself assumes (lognormal, same model as the option price)."""
    if years <= 0 or vol <= 0 or breakeven <= 0:
        return 0.0
    d2 = (math.log(spot / breakeven) + (rate - vol * vol / 2) * years) / (vol * math.sqrt(years))
    return _ncdf(d2) if kind == "call" else _ncdf(-d2)


def classify_setup(pct, high, low, price):
    """Is the big move holding (setup) or fading (wait)?
    Returns (kind or None, label, explanation)."""
    rng = high - low
    if rng <= 0:
        return None, "WAIT", "not enough movement today to read it."
    up = pct > 0
    hold = (price - low) / rng if up else (high - price) / rng
    mid = (high + low) / 2
    if hold >= HOLD_STRONG:
        if up:
            return ("call", "📗 CALL setup (momentum)",
                    f"It's holding near today's high. Idea: get in if it breaks {money(high)} (today's high)."
                    f" Get out if it drops back under {money(mid)} (middle of today's range).")
        return ("put", "📕 PUT setup (momentum)",
                f"It's holding near today's low. Idea: get in if it breaks {money(low)} (today's low)."
                f" Get out if it climbs back over {money(mid)} (middle of today's range).")
    if hold <= HOLD_WEAK:
        return (None, "⏳ NO CLEAN SETUP (fading)",
                "It moved big but already gave most of it back. Chasing now is a coin flip. Better to wait.")
    edge = high if up else low
    return (None, "⏳ NOT YET (choppy)",
            f"It's stuck in the middle of today's range. Wait: a {'call' if up else 'put'} only makes sense"
            f" if it breaks {money(edge)} (today's {'high' if up else 'low'}).")


def budget_pick(mkt, sym, kind, price, today, budget=BUDGET):
    """The option that fits the budget (1 contract) with the best chance of paying off."""
    best = None
    for exp in mkt.expiries(sym):
        days = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        if not PICK_MIN_DAYS <= days <= PICK_MAX_DAYS:
            continue
        calls, puts = mkt.chain(sym, exp)
        years = days / 365
        for r in (calls if kind == "call" else puts):
            p, k = premium_of(r), r.get("strike")
            if not k or p < 0.05 or p * 100 > budget or not (r.get("bid") or 0) > 0:
                continue
            iv = implied_vol(kind, p, price, k, years)
            if not iv:
                continue
            be = k + p if kind == "call" else k - p
            pr = prob_profit(kind, price, be, years, iv)
            if best is None or pr > best["prob"]:
                best = dict(strike=k, premium=p, expiry=exp, prob=pr, iv=iv)
    return best


def risk_tag(prob):
    """How risky a trade is, from its chance of paying off."""
    if prob >= CLEAR_CHANCE:
        return "🟢 CLEAR TRADE", "decent chance, normal risk"
    if prob >= RISKY_CHANCE:
        return "🟡 RISKY", "less than 1-in-3 chance, could go either way"
    return "🔴 LOTTERY TICKET", "small chance, big payout if it hits. Only money you're fine losing"


def budget_text(sym, kind, price, pick, today, budget=BUDGET):
    if not pick:
        return (f"💵 YOUR ${budget:.0f} PICK: none. No {sym} {kind} under ${budget:.0f} is for sale right now."
                " Skip this one.")
    n = int(budget // (pick["premium"] * 100))
    text = trade_math(sym, kind, price, pick["strike"], pick["premium"], pick["expiry"], today,
                      prob=pick["prob"], title=f"💵 YOUR ${budget:.0f} PICK ({risk_tag(pick['prob'])[0]}):")
    if n > 1:
        text += f"\n  (Your ${budget:.0f} buys {n} contracts = {money(n * pick['premium'] * 100)}. Multiply the numbers by {n}.)"
    return text + "\n  Simple rule: sell if the option doubles, get out if it loses half."


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

    def bars(self, sym):
        """Daily candles (date, open, high, low, close), oldest first. Today's is live."""
        if ("bars", sym) not in self._t:
            df = self.t(sym).history(period="1y", interval="1d", auto_adjust=False)
            df = df.dropna(subset=["Close"])
            self._t[("bars", sym)] = [dict(date=ts.date(), open=float(r.Open), high=float(r.High),
                                          low=float(r.Low), close=float(r.Close)) for ts, r in df.iterrows()]
        return self._t[("bars", sym)]

    def daily(self, sym):
        return [(b["date"], b["close"]) for b in self.bars(sym)]

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
    cheapest = sorted((s for s in snaps.values() if verdict(s["iv"], s["hv"])[1] == "CHEAP"),
                      key=lambda s: s["iv"] / s["hv"])
    for s in cheapest:  # first cheap stock with a $100 option that isn't a lottery ticket
        try:
            pick = budget_pick(mkt, s["sym"], "call", s["price"], today)
        except Exception as e:
            print(f"{s['sym']}: budget pick failed: {e}")
            continue
        if pick and pick["prob"] >= MIN_CHANCE:
            parts.append(f"🧮 THE MATH, example with cheap options today: {s['sym']} (an example, not a setup)\n"
                         + budget_text(s["sym"], "call", s["price"], pick, today))
            break
    parts.append("Alerts only, not advice. Never risk money you can't lose.")
    return "\n\n".join(parts)


def build_move_alert(mkt, s, pct, level, today, earn=None):
    sym, price = s["sym"], s["price"]
    bar = mkt.bars(sym)[-1]
    up = pct > 0
    head = "🚀" if up else "🩸"
    size = "HUGE" if level == 2 else "BIG"
    prev = s["days"][-2][1]
    kind, label, why = classify_setup(pct, bar["high"], bar["low"], price)
    parts = [f"{head} {size} MOVE: {sym} {'UP' if up else 'DOWN'} {pct:+.1f}% today",
             f"Now {money(price)} (yesterday closed at {money(prev)}). Today's range {money(bar['low'])} to {money(bar['high'])}."]
    h = mkt.headline(sym)
    if h:
        parts.append(f"📰 News: {h}")
    parts.append(f"🎯 {label}\n{why}")
    if kind:
        pick = budget_pick(mkt, sym, kind, price, today)
        if pick:
            tag, meaning = risk_tag(pick["prob"])
            parts[0] += f"\n{tag}: {meaning}"
        parts.append(budget_text(sym, kind, price, pick, today))
    parts.append(verdict_line(sym, s["iv"], s["hv"]))
    if earn is not None and 0 <= (earn - today).days <= 3:
        parts.append(f"📣 Heads up: {sym} reports earnings {nice_date(earn)} ({when_word(earn, today)})."
                     " Option prices drop right after earnings, even if you guess right.")
    parts.append("Alerts only, not advice. Only risk money you're OK losing.")
    return "\n\n".join(parts), kind


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
    me = tg("getMe").get("result", {}).get("username")
    updates = tg("getUpdates").get("result", [])
    print(f"Bot @{me} (own options key: {'yes' if os.environ.get('OPTIONS_TELEGRAM_BOT_TOKEN') else 'NO'}),"
          f" {len(updates)} new messages: {[(u.get('message') or {}).get('chat', {}).get('type') for u in updates]}")
    for u in reversed(updates):
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
    # Fresh memory (first run on main, or the cache was cleared): record what already
    # happened today without sending it again, so nobody gets repeat alerts.
    quiet = not state and not FORCE
    chat_id = find_chat_id(state)
    if not chat_id and not DRY_RUN:
        print("No chat yet: add the bot to the Options Alerts group and send /options there")
        save_state(state)
        return
    if state.pop("new_chat", False) and not quiet:
        send("✅ Options alerts are ON for this chat.\nEvery weekday: a morning brief at 8:45am New York,"
             " plus big-move alerts with a call or put setup that fits a $100 budget.\nAlerts only, not advice.", chat_id, state)
    weekday = today.weekday() < 5
    watch = load_watchlist()
    mkt = Market()
    sent = state.setdefault("sent", {})
    day = today.isoformat()
    for k in [k for k in sent if not k.startswith(day)]:
        del sent[k]

    if FORCE in ("brief", "all") or (weekday and now.time() >= BRIEF_START and state.get("brief") != day
                                     and now.time() < SCAN_END):
        chat_id = send(build_brief(mkt, watch, today), None if quiet else chat_id, state) or chat_id
        state["brief"] = day
        save_state(state)

    if FORCE in ("scan", "all") or (weekday and SCAN_START <= now.time() <= SCAN_END):
        for sym in watch:
            try:
                days = mkt.daily(sym)
                if len(days) < 3 or (days[-1][0] != today and not FORCE):
                    continue  # no trading yet today
                pct = (days[-1][1] / days[-2][1] - 1) * 100
                lvl = move_level(pct, MOVE_PCT_ETF if sym in ETFS else MOVE_PCT_STOCK)
                key = f"{day}:move:{sym}:{'up' if pct > 0 else 'down'}"
                print(f"{sym}: {pct:+.2f}% level {lvl}")
                if not lvl:
                    continue
                bar = mkt.bars(sym)[-1]
                setup = classify_setup(pct, bar["high"], bar["low"], days[-1][1])[0]
                had = sent.get(key) or {}
                if not isinstance(had, dict):  # older format: a number, already alerted
                    had = dict(lvl=had, setup=True)
                # Alert on a new or bigger move, or when a setup shows up after a "wait" alert.
                if FORCE or lvl > had.get("lvl", 0) or (setup and not had.get("setup")):
                    s = snapshot(mkt, sym, today)
                    earn = None if sym in ETFS else mkt.earnings_date(sym, today)
                    text, kind = build_move_alert(mkt, s, pct, lvl, today, earn)
                    chat_id = send(text, None if quiet else chat_id, state) or chat_id
                    sent[key] = dict(lvl=max(lvl, had.get("lvl", 0)), setup=bool(kind) or had.get("setup", False))
            except Exception as e:
                print(f"{sym}: scan failed: {e}")
    save_state(state)


if __name__ == "__main__":
    main()
