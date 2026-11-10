"""Gold 30-minute breakout alert bot (alerts only, never trades).

Rules:
1. Consolidation: the BOX_BARS 30-min candles before the latest one stayed in a tight
   range (box height <= TIGHT_MULT x the average candle size).
   Box top = resistance, box bottom = support.
2. Breakout: the latest finished candle CLOSES above resistance (BUY) or below support (SELL).
3. Impulsive: that candle is big (>= IMPULSE_MULT x average size) or has high volume
   (>= IMPULSE_MULT x average volume).
4. Only for candles that close between 7:00am and 4:00pm New York time.
5. Entry trigger: after a breakout alert, every 5-minute check watches for price to break
   the breakout candle's high (BUY) or low (SELL) and sends an "ENTRY TRIGGERED" alert.
   This watch expires after ENTRY_WINDOW_MIN minutes.
"""
import json
import os
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import requests

NY = ZoneInfo("America/New_York")
TICKER = os.environ.get("TICKER", "GC=F")  # gold futures: used for volume (spot XAU/USD has none)
BAR = timedelta(minutes=30)
BOX_BARS = int(os.environ.get("BOX_BARS", "6"))        # 6 candles = 3 hours
AVG_BARS = int(os.environ.get("AVG_BARS", "20"))
TIGHT_MULT = float(os.environ.get("TIGHT_MULT", "2.5"))
IMPULSE_MULT = float(os.environ.get("IMPULSE_MULT", "1.5"))
START = time(int(os.environ.get("START_HOUR", "7")), 0)
END = time(int(os.environ.get("END_HOUR", "16")), 0)
STATE_FILE = ".state/state.json"
ENTRY_WINDOW = timedelta(minutes=int(os.environ.get("ENTRY_WINDOW_MIN", "60")))


def check(bars, now):
    """bars: list of dicts with start (aware datetime), open, high, low, close, volume, oldest first.
    Returns an alert dict or None."""
    done = [b for b in bars if b["start"] + BAR <= now]
    if len(done) < BOX_BARS + AVG_BARS + 1:
        return None
    last = done[-1]
    close_ny = (last["start"] + BAR).astimezone(NY).time()
    if not (START <= close_ny <= END):
        return None
    box = done[-BOX_BARS - 1:-1]
    hist = done[-AVG_BARS - 1:-1]
    top, bottom = max(b["high"] for b in box), min(b["low"] for b in box)
    avg_size = sum(b["high"] - b["low"] for b in hist) / len(hist)
    vols = [b["volume"] for b in hist if b["volume"]]
    avg_vol = sum(vols) / len(vols) if vols else 0
    if avg_size <= 0 or (top - bottom) > TIGHT_MULT * avg_size:
        return None  # not consolidating
    if last["close"] > top:
        side, entry = "BUY", last["high"]
    elif last["close"] < bottom:
        side, entry = "SELL", last["low"]
    else:
        return None
    size = last["high"] - last["low"]
    big = size >= IMPULSE_MULT * avg_size
    loud = avg_vol > 0 and last["volume"] >= IMPULSE_MULT * avg_vol
    if not (big or loud):
        return None
    why = []
    if big:
        why.append(f"candle {size / avg_size:.1f}x normal size")
    if loud:
        why.append(f"volume {last['volume'] / avg_vol:.1f}x normal")
    return dict(side=side, close=last["close"], top=top, bottom=bottom, entry=entry,
                why=", ".join(why), at=(last["start"] + BAR).astimezone(NY))


def format_alert(a):
    icon = "🟢" if a["side"] == "BUY" else "🔴"
    level = "resistance" if a["side"] == "BUY" else "support"
    trigger = "above" if a["side"] == "BUY" else "below"
    return (f"{icon} XAU/USD {a['side']} BREAKOUT ({a['at']:%-I:%M %p} NY, 30-min)\n"
            f"Closed {trigger} {level}: ${a['close']:,.2f}\n"
            f"Box: support ${a['bottom']:,.2f} / resistance ${a['top']:,.2f}\n"
            f"Impulsive: {a['why']}\n"
            f"Entry idea: break of last candle {'high' if a['side'] == 'BUY' else 'low'} ${a['entry']:,.2f}")


def check_entry(pending, bars5, now):
    """pending: dict(side, entry, since ISO time). bars5: 5-min bars (the latest may still be forming).
    Returns ('triggered', price) / ('expired', None) / (None, None)."""
    since = datetime.fromisoformat(pending["since"])
    recent = [b for b in bars5 if b["start"] + timedelta(minutes=5) > since and b["start"] <= now]
    for b in recent:
        if pending["side"] == "BUY" and b["high"] > pending["entry"]:
            return "triggered", b["high"]
        if pending["side"] == "SELL" and b["low"] < pending["entry"]:
            return "triggered", b["low"]
    if now - since > ENTRY_WINDOW:
        return "expired", None
    return None, None


def format_entry(pending, price):
    icon = "🚀" if pending["side"] == "BUY" else "🔻"
    word = "above the high" if pending["side"] == "BUY" else "below the low"
    return (f"{icon} XAU/USD {pending['side']} ENTRY TRIGGERED\n"
            f"Price broke {word} of the breakout candle (${pending['entry']:,.2f}).\n"
            f"Price reached: ${price:,.2f}")


YAHOO_INTERVAL = {"30min": "30m", "5min": "5m"}


def _yahoo_bars(ticker, interval="30min"):
    import yfinance as yf
    df = yf.download(ticker, period="5d", interval=YAHOO_INTERVAL[interval], auto_adjust=False, progress=False)
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return [dict(start=ts.to_pydatetime(), open=float(r.Open), high=float(r.High), low=float(r.Low),
                 close=float(r.Close), volume=float(r.Volume)) for ts, r in df.dropna().iterrows()]


def _spot_bars(api_key, interval="30min"):
    """XAU/USD spot 30-min candles from Twelve Data (free key). Spot gold has no real volume."""
    r = requests.get("https://api.twelvedata.com/time_series",
                     params=dict(symbol="XAU/USD", interval=interval, outputsize=100,
                                 timezone="UTC", apikey=api_key), timeout=30)
    r.raise_for_status()
    data = r.json()
    if "values" not in data:
        raise RuntimeError(f"Twelve Data error: {data}")
    utc = ZoneInfo("UTC")
    return [dict(start=datetime.fromisoformat(v["datetime"]).replace(tzinfo=utc),
                 open=float(v["open"]), high=float(v["high"]), low=float(v["low"]),
                 close=float(v["close"]), volume=0.0) for v in reversed(data["values"])]


def get_bars_history():
    """As much 30-min history as the free sources give (about 60 days, limited by futures volume)."""
    import yfinance as yf
    df = yf.download(TICKER, period="60d", interval="30m", auto_adjust=False, progress=False)
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    fut = [dict(start=ts.to_pydatetime(), open=float(r.Open), high=float(r.High), low=float(r.Low),
                close=float(r.Close), volume=float(r.Volume)) for ts, r in df.dropna().iterrows()]
    key = os.environ.get("TWELVEDATA_API_KEY")
    if not key:
        return fut
    r = requests.get("https://api.twelvedata.com/time_series",
                     params=dict(symbol="XAU/USD", interval="30min", outputsize=5000,
                                 timezone="UTC", apikey=key), timeout=60)
    r.raise_for_status()
    data = r.json()
    if "values" not in data:
        raise RuntimeError(f"Twelve Data error: {data}")
    utc = ZoneInfo("UTC")
    vol = {b["start"]: b["volume"] for b in fut}
    spot = [dict(start=datetime.fromisoformat(v["datetime"]).replace(tzinfo=utc),
                 open=float(v["open"]), high=float(v["high"]), low=float(v["low"]),
                 close=float(v["close"])) for v in reversed(data["values"])]
    first = fut[0]["start"] if fut else None
    out = []
    for b in spot:
        if first and b["start"] < first:
            continue
        b["volume"] = vol.get(b["start"], 0.0)
        out.append(b)
    return out


def get_bars_5min():
    key = os.environ.get("TWELVEDATA_API_KEY")
    return _spot_bars(key, "5min") if key else _yahoo_bars(TICKER, "5min")


def get_bars():
    """XAU/USD spot prices (if a Twelve Data key is set) with volume borrowed from gold
    futures (GC=F), which trade the same gold at the same times. Without a key it uses futures only."""
    fut = _yahoo_bars(TICKER)
    key = os.environ.get("TWELVEDATA_API_KEY")
    if not key:
        return fut
    vol = {b["start"]: b["volume"] for b in fut}
    spot = _spot_bars(key)
    for b in spot:
        b["volume"] = vol.get(b["start"], 0.0)
    return spot


def send_telegram(text):
    r = requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                      data={"chat_id": os.environ["TELEGRAM_CHAT_ID"],
                            "text": text + "\n\nAlert only, not financial advice."}, timeout=30)
    r.raise_for_status()


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def main():
    now = datetime.now(tz=ZoneInfo("UTC"))
    state = load_state()
    messages = []
    bars = get_bars()
    alert = check(bars, now)
    if alert:
        key = f"{alert['at']:%Y-%m-%d %H:%M} {alert['side']}"
        if key != state.get("last_alert"):
            state["last_alert"] = key
            state["pending"] = dict(side=alert["side"], entry=alert["entry"], since=alert["at"].isoformat())
            messages.append(format_alert(alert))
    if state.get("pending"):
        result, price = check_entry(state["pending"], get_bars_5min(), now)
        if result == "triggered":
            messages.append(format_entry(state["pending"], price))
        if result:
            state.pop("pending")
    save_state(state)
    if not messages and os.environ.get("FORCE_MESSAGE", "").lower() == "true":
        last = bars[-1] if bars else None
        messages.append("ℹ️ Gold bot test: connected and working. No breakout right now." + (
            f"\nLatest {'XAU/USD' if os.environ.get('TWELVEDATA_API_KEY') else 'gold futures'} price: ${last['close']:,.2f}" if last else ""))
    if not messages:
        print("Nothing to alert.")
    for msg in messages:
        print(msg)
        send_telegram(msg)


if __name__ == "__main__":
    main()
