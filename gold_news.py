"""Gold news alerts for the Gold Alerts Telegram group (news only, never trades).

Gold (XAU/USD) trades like a forex pair: it's gold priced in US dollars, so it moves on the
same US news that forex traders watch. This sends:
1. 🗓 Morning brief (first check after 7am NY on weekdays): today's red and orange US dollar
   events from Forex Factory's calendar, and the big ones coming up.
2. ⚠️ Heads-up 10-30 minutes before each red (high impact) US event. Raja stays
   out from 15 min before to 5 min after, because gold can jump $20-40 in seconds.
3. 📰 Gold headlines around 8am and 12pm, each with "why it matters for gold".
"""
import os
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
FORCE = os.environ.get("FORCE_NEWS", "")  # "brief", "headlines" or "all": send now

sys.path.insert(0, str(Path(__file__).parent / "options"))
try:  # one calendar for both bots
    from options_bot import CPI, FOMC, JOBS, parse_news
except Exception:  # pragma: no cover
    FOMC, CPI, JOBS, parse_news = [], [], [], None

EVENTS = sorted(
    [(d, time(14, 0), "🏦 Fed interest rate decision", "The biggest gold mover. A rate cut usually pushes gold up, a hike or a 'no cut' pushes it down.") for d in FOMC]
    + [(d, time(8, 30), "🧾 Inflation report (CPI)", "Hot inflation means fewer rate cuts: stronger dollar, gold usually drops. Cool inflation usually lifts gold.") for d in CPI]
    + [(d, time(8, 30), "👷 Jobs report (NFP)", "Strong jobs mean a stronger dollar and gold usually drops. Weak jobs usually lift gold.") for d in JOBS])
LOOKAHEAD_DAYS = 7
WARN_FROM, WARN_TO = timedelta(minutes=10), timedelta(minutes=30)
BRIEF_AT, BRIEF_LATEST = time(7, 0), time(10, 0)  # a brief after 10am is old news
HEADLINE_SLOTS = [(time(8, 0), time(8, 40)), (time(12, 0), time(12, 40))]
HEADLINE_COUNT = 3
MAX_AGE_H = 12
NEWS_TICKERS = ["GC=F", "GLD"]

GOLD_WORDS = (r"gold|bullion|precious metal|xau|\bfed\b|powell|rate cut|rate hike|interest rate|dollar"
              r"|inflation|\bcpi\b|payroll|jobs report|treasury yield|central bank|safe.haven|tariff")
JUNK = r"^bc-|retire|\bira\b|401\(k\)|should you buy|stocks? to buy|best .*stock|my top|coin dealer|jewel"
WHY = [
    (r"\bfed\b|powell|rate cut|rate hike|interest rate", "Fed and interest rates are the biggest gold driver. Lower rates usually lift gold, higher rates usually push it down."),
    (r"inflation|\bcpi\b|\bppi\b|prices", "Inflation news changes rate-cut hopes. Hot numbers usually hurt gold, cool numbers help it."),
    (r"jobs|payroll|unemployment|labor", "Jobs news moves the dollar. Strong jobs usually hurt gold, weak jobs help it."),
    (r"dollar|greenback|\bdxy\b", "Gold and the dollar are a seesaw: dollar up usually means gold down, and the other way around."),
    (r"yield|treasur|bond", "Higher bond yields make gold less attractive (it pays no interest), so gold often dips."),
    (r"central bank|china|pboc|reserve", "Central banks buying gold is a big reason gold has been strong."),
    (r"war|attack|missile|conflict|tension|crisis|geopolit|safe.haven", "Scary world news sends people to gold as a safe place, which usually pushes it up."),
    (r"tariff|trade war|sanction", "Trade fights add worry, which often helps gold."),
    (r"record|all.time high|surge|soar|rall(y|ies)|jump", "Gold is moving up fast. Watch for a range or rejection setup before jumping in."),
    (r"plunge|tumble|slide|sink|drop|fall|selloff|sell-off", "Gold is dropping fast. Wait for a setup instead of guessing the bottom."),
]


FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"  # Forex Factory's weekly calendar
FF_REFRESH = timedelta(hours=3)
FF_IMPACT = {"High": "🔴", "Medium": "🟠"}
EVENT_WHY = [
    (r"fomc|federal funds|fed chair|powell|interest rate", "The biggest gold mover. A rate cut usually pushes gold up, a hike or a 'no cut' pushes it down."),
    (r"\bcpi\b|\bppi\b|\bpce\b|inflation", "Hot inflation means fewer rate cuts: stronger dollar, gold usually drops. Cool inflation usually lifts gold."),
    (r"non-farm|payroll|unemployment|jobless|employment|jolts|adp", "Strong jobs mean a stronger dollar and gold usually drops. Weak jobs usually lift gold."),
    (r"gdp", "Strong growth usually means a stronger dollar and weaker gold, and the other way around."),
    (r"retail sales|ism|pmi|consumer", "A surprise here moves the dollar, and gold moves the other way."),
    (r"fomc member|speaks|testif", "A Fed official talking can hint at rate moves. Gold can swing on one sentence."),
]


def _event_why(title):
    t = title.lower()
    return next((w for pat, w in EVENT_WHY if re.search(pat, t)), "Big US news: the dollar can jump, and gold with it.")


def fetch_ff():
    """This week's USD events from Forex Factory: [{when, title, impact, forecast, previous}]."""
    import requests
    r = requests.get(FF_URL, timeout=20, headers={"User-Agent": "gold-alert-bot"})
    r.raise_for_status()
    out = []
    for e in r.json():
        if e.get("country") != "USD" or e.get("impact") not in FF_IMPACT:
            continue
        try:
            when = datetime.fromisoformat(e["date"]).astimezone(NY)
        except (KeyError, ValueError):
            continue
        out.append(dict(when=when.isoformat(), title=e.get("title", ""), impact=e["impact"],
                        forecast=e.get("forecast") or "", previous=e.get("previous") or ""))
    return out


def ff_events(state, now, fetch=fetch_ff):
    """Cached Forex Factory events (refreshed every few hours). None if unavailable."""
    st = state.setdefault("news", {})
    cache = st.get("ff") or {}
    fresh = cache.get("at") and now - datetime.fromisoformat(cache["at"]) < FF_REFRESH
    if not fresh:
        try:
            st["ff"] = cache = dict(at=now.isoformat(), events=fetch())
        except Exception as exc:
            print(f"Forex Factory fetch failed: {exc}")
            if not cache:
                return None
    return cache.get("events")


def events_between(start, end, ff=None, min_impact="Medium"):
    """[(when, name, why, extra)] in [start, end). Uses Forex Factory when we have it,
    plus our own Fed/CPI/jobs list for days the weekly feed doesn't cover."""
    out, covered = [], set()
    for e in ff or []:
        when = datetime.fromisoformat(e["when"])
        covered.add(when.date())
        if not (start <= when < end) or (min_impact == "High" and e["impact"] != "High"):
            continue
        bits = [f"forecast {e['forecast']}" if e["forecast"] else "", f"last {e['previous']}" if e["previous"] else ""]
        extra = ", ".join(b for b in bits if b)
        out.append((when, f"{FF_IMPACT[e['impact']]} {e['title']}", _event_why(e["title"]), extra))
    for d, t, name, why in EVENTS:
        when = datetime.combine(date.fromisoformat(d), t, tzinfo=NY)
        if start <= when < end and (not ff or when.date() > max(covered, default=date.min)):
            out.append((when, name, why, ""))
    return sorted(out, key=lambda x: x[0])


def build_brief(now, ff=None):
    today0 = datetime.combine(now.date(), time(0), tzinfo=NY)
    today = events_between(today0, today0 + timedelta(days=1), ff)
    later = events_between(today0 + timedelta(days=1), today0 + timedelta(days=LOOKAHEAD_DAYS + 1), ff, "High")
    lines = [f"🗓 GOLD NEWS TODAY · {now:%a %b %-d} (Forex Factory, US dollar news)"]
    if today:
        for when, name, why, extra in today:
            lines.append(f"{name} at {when:%-I:%M %p}" + (f" ({extra})" if extra else "") + f". {why}")
        lines.append("Raja stays out from 15 min before to 5 min after big news. Gold can jump $20-40 in seconds.")
    else:
        lines.append("No big US news today. Normal trading day for gold.")
    if later:
        lines.append("Coming up: " + "; ".join(f"{n[2:]} {w:%a %b %-d}" for w, n, _, _ in later[:6]))
    return "\n".join(lines)


def build_warning(when, name, why, now, extra=""):
    mins = max(1, round((when - now).total_seconds() / 60))
    return (f"⚠️ BIG NEWS IN {mins} MIN · {name} at {when:%-I:%M %p}\n"
            + (f"Expected: {extra}\n" if extra else "") +
            f"Gold can jump $20-40 in seconds. Raja stays out from 15 min before to 5 min after.\n"
            f"{why}")


def relevant(n):
    t = n["title"].lower()
    return not re.search(JUNK, t) and bool(re.search(GOLD_WORDS, t))


def why_for_gold(title):
    t = title.lower()
    return next((w for pat, w in WHY if re.search(pat, t)), "Worth knowing before you trade gold today.")


def pick_headlines(items, seen, now_ts, count=HEADLINE_COUNT):
    out, titles = [], set()
    fresh = [n for n in items if n.get("ts") and now_ts - n["ts"] <= MAX_AGE_H * 3600 and relevant(n)]
    for n in sorted(fresh, key=lambda n: -n["ts"]):
        k = n["title"].lower()
        if k in titles or k in seen:
            continue
        titles.add(k)
        out.append(n)
        if len(out) == count:
            break
    return out


def build_headline(n):
    src = f" ({n['source']})" if n.get("source") else ""
    lines = ["📰 GOLD NEWS", f"{n['title']}{src}", f"Why it matters: {why_for_gold(n['title'])}"]
    if n.get("link"):
        lines.append(n["link"])
    return "\n".join(lines)


def fetch_headlines():
    import yfinance as yf
    items = []
    for sym in NEWS_TICKERS:
        try:
            raw = yf.Ticker(sym).news or []
        except Exception:
            continue
        items += [n for n in (parse_news(it, sym) for it in raw) if n] if parse_news else []
    return items


def news_messages(now_utc, state, fetch=fetch_headlines, fetch_cal=fetch_ff):
    """Returns the news messages due now and updates `state` (a dict) so nothing repeats."""
    now = now_utc.astimezone(NY)
    day = now.date().isoformat()
    st = state.setdefault("news", {})
    msgs = []
    weekday = now.weekday() < 5
    ff = ff_events(state, now, fetch_cal)
    if FORCE in ("brief", "all") or (weekday and BRIEF_AT <= now.time() <= BRIEF_LATEST and st.get("brief") != day):
        st["brief"] = day
        msgs.append(build_brief(now, ff))
    warned = set(st.get("warned", []))
    for when, name, why, extra in events_between(now + WARN_FROM, now + WARN_TO, ff, "High"):
        key = f"{when.isoformat()} {name}"
        if key not in warned:
            warned.add(key)
            msgs.append(build_warning(when, name, why, now, extra))
    st["warned"] = sorted(warned)[-20:]
    slot = next((i for i, (a, b) in enumerate(HEADLINE_SLOTS) if a <= now.time() <= b), None)
    if FORCE in ("headlines", "all") or (weekday and slot is not None and st.get("slot") != f"{day}:{slot}"):
        st["slot"] = f"{day}:{slot}"
        seen_all = st.get("seen", {})
        seen = set(seen_all.get(day, []))
        pick = pick_headlines(fetch(), seen, now_utc.timestamp())
        msgs += [build_headline(n) for n in pick]
        st["seen"] = {day: sorted(seen | {n["title"].lower() for n in pick})}
    return msgs
