# Gold (XAU/USD) breakout alert bot

Sends a Telegram alert when gold breaks out of a 30-minute consolidation between 7 and 10am New York time. It **never places trades**.

## The rules
1. **Consolidation:** the last 6 thirty-minute candles (3 hours) stayed in a tight box, no taller than 2.5x a normal candle. Box top = resistance, box bottom = support.
2. **Breakout:** a 30-min candle **closes** above resistance (BUY) or below support (SELL).
3. **Impulsive:** that candle is at least 1.5x a normal candle's size, **or** has at least 1.5x normal volume.
4. **Time:** only candles closing from 7:00 to 10:00am New York time.
5. **Entry trigger:** after a breakout alert, the bot checks every 5 minutes. When price breaks the breakout candle's high (BUY) or low (SELL), it sends an **ENTRY TRIGGERED** alert. It stops watching after 60 minutes.

Each alert shows the price, the box levels, why it counts as impulsive, and an entry idea (break of the breakout candle's high or low).

## Prices
- **XAU/USD spot prices** come from Twelve Data (free key from twelvedata.com).
- **Volume** comes from gold futures (GC=F), because spot gold has no single volume number.
- Without a Twelve Data key, it uses gold futures prices, which run a few dollars different from spot.

Free data can run a few minutes late, and GitHub's timer can also start late, so an alert may arrive a few minutes late. The bot runs every 5 minutes during the morning window.

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
