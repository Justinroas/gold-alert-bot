# Gold (XAU/USD) breakout alert bot

Sends a Telegram alert when gold breaks out of a 30-minute consolidation between 7am and 4pm New York time. It **never places trades**.

## The rules
1. **Consolidation:** the last 6 thirty-minute candles (3 hours) stayed in a tight box, no taller than 2.5x a normal candle. Box top = resistance, box bottom = support.
2. **Breakout:** a 30-min candle **closes** above resistance (BUY) or below support (SELL).
3. **Impulsive:** that candle is at least 1.5x a normal candle's size, **or** has at least 1.5x normal volume.
4. **Time:** only candles closing from 7:00am to 4:00pm New York time.
5. **Entry trigger:** after a breakout alert, the bot checks every 10 minutes. When price breaks the breakout candle's high (BUY) or low (SELL), it sends an **ENTRY TRIGGERED** alert. It stops watching after 60 minutes.

Each alert shows the price, the box levels, why it counts as impulsive, and an entry idea (break of the breakout candle's high or low).

## Prices
- **XAU/USD spot prices** come from Twelve Data (free key from twelvedata.com).
- **Volume** comes from gold futures (GC=F), because spot gold has no single volume number.
- Without a Twelve Data key, it uses gold futures prices, which run a few dollars different from spot.

Free data can run a few minutes late, and GitHub's timer can also start late, so an alert may arrive a few minutes late. The bot runs every 10 minutes during the 7am to 4pm window.

## Setup
1. **Telegram bot:** in Telegram, message **@BotFather** (blue checkmark), send `/newbot`, and copy the token. Keep it secret.
2. **Add it to your group** and send `/start@YourBotUsername` there.
3. **Chat ID:** open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the number after `"chat":{"id":` (it starts with -100).
4. **Twelve Data key:** sign up free at twelvedata.com and copy your API key.
5. **GitHub:** make a repo and upload these files, keeping the `.github/workflows` folder.
6. **Secrets:** in the repo, go to Settings → Secrets and variables → Actions → New repository secret:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `TWELVEDATA_API_KEY`
7. **Test it:** in the Actions tab, open Gold alert → Run workflow. A test message should show up in the group.

## Tuning
To change a setting, edit the numbers near the top of `alert.py`: `BOX_BARS` (6 candles), `TIGHT_MULT` (2.5), `IMPULSE_MULT` (1.5).

## Strategy 2: Raja Banks break & retest (15-min)

Every alert starts with the strategy name (📘 30-minute breakout or 📗 Raja strategy).
Raja alerts (rules in `raja.py`): between 7 and 11am NY, a 15-min candle closes strongly through
yesterday's high/low, the Asia high/low or the London high/low, then price comes back to that
level and holds. The alert gives entry, stop loss (just past the retest candle, max 50 pips)
and take profit 1 / 2 (35 / 50 pips; 1 pip = $0.10 on gold).

## Options alert bot (separate, in `options/`)

Sends options alerts to its own Telegram group, **Options Alerts**, using the same bot (never to the gold group).
Code: `options/options_bot.py`, workflow: `.github/workflows/options-alert.yml`. It never trades.

- **Morning brief** (weekdays after 8:45am NY): upcoming earnings, Fed decisions, CPI and jobs reports;
  how big a move options expect from each earnings; whether each stock's options are CHEAP, FAIR or PRICEY;
  and the math for one example trade.
- **Big move alert** (9:45am-4pm): a stock moves 3% (ETFs 1.5%) from yesterday's close. A second alert at 6% / 3%.
  It says if there's a setup: **CALL** (up and holding near today's high), **PUT** (down and holding near
  today's low), or **wait** (fading or choppy; you get a new alert if a setup forms later). For a setup it picks
  the option under the budget (`BUDGET`, default $100 for 1 contract, expiring in 7-45 days) with the best
  chance of paying off, and shows cost, breakeven, that chance, and max loss.

Cheap or pricey = implied volatility (what the option price assumes) vs how much the stock normally moves
(average of its last-month and last-year volatility). Data: Yahoo Finance (free, about 15 min delayed for options).

**Setup:** make a Telegram group called Options Alerts, add @justinroas_gold_alert_bot,
and send `/options@justinroas_gold_alert_bot` in the group. The bot finds that chat by itself. (Or set the `OPTIONS_TELEGRAM_CHAT_ID` secret.)
**Watchlist:** edit `options/watchlist.txt`. **Timing:** it runs right after each gold-bot run, with a GitHub backup timer.
**Test:** Actions → Options alert → Run workflow → pick `brief`.
