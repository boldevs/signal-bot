# Signal bot

Sends LONG / SHORT alerts for coins to Telegram and measures afterwards whether each alert would have made money.

## Language

**Signal**:
One LONG or SHORT alert for one coin, with an entry price, a stop loss and a take profit. Leverage plans, event alerts and macro alerts are not signals.
_Avoid_: Trade, call, alert (too broad)

**Entry**:
The close price of the candle that produced the signal. The "better price" entry zone is not used when judging results.
_Avoid_: Fill, open price

**Win**:
A signal whose price reached the take profit before the stop loss.
_Avoid_: Success, hit

**Loss**:
A signal whose price reached the stop loss before the take profit, or reached both within the same candle.
_Avoid_: Fail, miss

**Expired**:
A signal that reached neither level within the time limit. It is shown separately and is not counted in the win rate.
_Avoid_: Timeout, draw

**Open**:
A signal that has not yet won, lost or expired.
_Avoid_: Pending, active

**R**:
The stop distance of a signal, used as the unit of result. A loss is -1R; a win is +1.5R at the current reward/risk (`REWARD_RISK`).
_Avoid_: Points, pips

**Win rate**:
Wins divided by wins plus losses. Expired and open signals are left out.
_Avoid_: Accuracy, hit rate

**Skipped signal**:
A signal the filters held back (trend, news or Claude did not agree, confidence too low, a stronger coin in the same direction, or the per-run / per-day limit). It is recorded and judged like a sent signal but never announced, and kept out of the normal results; `/stats` shows it under "Filters".
_Avoid_: Rejected, filtered trade

**Confidence**:
HIGH when the trend, the news and Claude's review all agree with the side; MEDIUM when the news is neutral. Only HIGH is sent by default (`SEND_CONFIDENCE`).
_Avoid_: Score, strength

**History**:
The saved record of every signal and its result, separate from the bot's dedupe memory.
_Avoid_: Log, database

**Daily bias**:
The morning gold / forex message: LONG / SHORT / WAIT per pair from daily candles, with key levels and big news. A bias is not a signal and is not tracked.
_Avoid_: Daily signal

**Skipped signal**:
A signal the filters held back (trend, news, Claude, confidence, max per run / day). Logged quietly and judged like a real one, so `/stats` can show whether the filters help, but never announced and not counted in the normal win rate.
_Avoid_: Rejected, filtered
