# Crypto Signal Bot (v1)

Sends **LONG / SHORT / WAIT** alerts for **BTC, ETH, SOL, XRP, BNB, DOGE, ADA, AVAX and LINK** (15-minute candles, day trading: only WITH the 1-hour trend) to your **Telegram channel**.

- Python checks prices every 5 minutes (free).
- Only when a signal appears, **Claude** (your Pro plan, via Claude Code) reads the news and reviews the signal.
- **Fewer, better signals**: a LONG/SHORT is sent only when the trend, the news and Claude all agree
  (Claude CAUTION = no signal), labelled **HIGH** / **MEDIUM** confidence (only HIGH by default).
  Max 1 per run and 3 per day: the strongest coin (highest ADX); other coins with the same direction are
  listed as "also signaling". Signals held back are logged quietly so `/stats` shows whether the filters help.
- If Claude is unavailable, the free **VADER** backup is used.
- Also sends **EVENT ALERTS** 🚀/💣 when news mentions a listing, ETF approval, hack, delisting, etc. (any coin; keyword rules, then one Claude call per run confirms them).
- Also sends **MACRO ALERTS** 🌍 for big world news (Fed, inflation, tariffs, war...) judged by Claude, with BTC/ETH/SOL trade ideas.
- Every LONG/SHORT signal also sends one **plan per leverage range** (10x-20x, 20x-40x, 40x-60x, 60x-100x) with its own stop method, TP and liquidation.
- **Gold & forex** 🥇 (XAU/USD, EUR/USD, GBP/USD, USD/JPY) on your laptop: day-trading signals and a morning bias (see below).
- Crypto runs for free on **GitHub Actions**. Alerts only, it never places trades.

> Educational project. Not financial advice. Paper trade / testnet only.

## Files

| File | What it does |
|---|---|
| `bot/config.py` | All settings (coins, indicators, risk, news feeds). Edit here. |
| `bot/market_data.py` | Downloads candles (Binance public data, Kraken backup) |
| `bot/indicators.py` | EMA, RSI, ATR |
| `bot/signals.py` | The strategy: LONG / SHORT / WAIT rules |
| `bot/news.py` | Reads headlines from free RSS feeds |
| `bot/events.py` | Spots event headlines (listings, ETF approvals, hacks, delistings) for any coin |
| `bot/macro.py` | Finds big world news (Fed, tariffs, war...) and asks Claude if it moves markets |
| `bot/fx.py` | Gold / forex run: filters, limits, daily bias, results |
| `bot/fx_data.py` | Gold / forex candles (Twelve Data, Yahoo backup), market hours |
| `bot/fx_signals.py` | Gold / forex strategy |
| `bot/fx_news.py` | One Claude call: will the news push each pair's price up (+) or down (-)? |
| `bot/fx_calendar.py` | ForexFactory big-news calendar (Fed, CPI, NFP) |
| `bot/sentiment.py` | Asks Claude (or VADER backup) if news is bullish/bearish |
| `bot/telegram.py` | Formats and sends the alert |
| `bot/state.py` | Memory so you don't get duplicate alerts |
| `bot/__main__.py` | Main program and commands |
| `.github/workflows/signal-bot.yml` | Runs everything (crypto + gold/FX) on GitHub every 5 minutes |
| `tests/test_bot.py`, `tests/test_fx.py` | Offline tests (crypto, gold / forex) |

## Commands

```
python -m bot run --dry-run     # full check, prints alerts instead of sending
python -m bot run               # full check + send to Telegram (crypto + gold/forex)
python -m bot fx --dry-run      # gold / forex only, prints instead of sending
python -m bot loop              # laptop: full check every 5 minutes until Ctrl+C
python -m bot test-telegram     # send a test message
python -m bot test-claude       # check Claude Code works with your Pro login
python -m pytest -q             # run tests
```

## GitHub secrets needed

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`).
Never add `ANTHROPIC_API_KEY` (that bills the paid API).

See the setup guide for step-by-step instructions.


python -m pytest -q
python -m bot run --dry-run

2. Make sure a .env file exists in that folder. The script fails without it. It should contain:
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
CLAUDE_CODE_OAUTH_TOKEN=your_token
3. Run the script:
.\run-local.ps1
4. Stop it with Ctrl+C.
## Results and `/stats`

Every alert you receive is saved in `history.json` and judged later: take profit first = WIN, stop loss first = LOSS, neither in 4 hours (15m signals) = EXPIRED. The bot posts a message when a signal finishes. Type `/stats` in your Telegram chat to get the win rate, average R, per-coin results and open signals (answered on the next run, within about 5 minutes). Run the bot in one place at a time, because two copies would take each other's Telegram messages. Settings are in `bot/config.py` (section 10).

## Coin buttons

Messages about one coin (a signal, an event, a news alert naming one coin) have buttons:
- **BTC 15m · BTC 30m · BTC 1h**: a fresh look at that coin on that candle size (price, trend, RSI, ADX, levels,
  plus a Claude news verdict). A look only: not recorded in `/stats`.
- **📊 BTC stats**: results of that coin only (win rate, open signals, Claude's calls).
- **10x ... 100x**: the plan for that coin at exactly that leverage (entry, stop, target, liquidation,
  % of margin you lose or gain). No Claude call.

News alerts can name **any coin** in `config.ALL_COINS` (the watched coins plus `EXTRA_COINS`). Macro alerts let Claude
pick up to 3 coins and show a **🔎 coin** button for each. For all coins at once, type `/stats`.
With `python -m bot watch` (`run-local.ps1`) a tap is answered within seconds; on GitHub Actions on the next 10-minute run.

Filters that cut false signals (`config.py`): `ADX_MIN` (no signal in a sideways market),
`HTF_FILTER` / `HTF_STRICT` (only trade with the bigger candle's trend), `FLIP_COOLDOWN_CANDLES`,
`MAX_SIGNALS_PER_RUN`, `MAX_SIGNALS_PER_DAY`, `SEND_CONFIDENCE`.

## Gold & forex (laptop)

Watches **XAU/USD, EUR/USD, GBP/USD, USD/JPY** for day trades that close within a few hours (never overnight).

- **Candles**: Twelve Data (free key from twelvedata.com, `TWELVEDATA_API_KEY`), Yahoo Finance as backup
  (gold then comes from the COMEX future, a few dollars off spot). To fit the free limit (8 requests/min,
  800/day) the bot downloads only 15m candles, once per new candle, and builds the 1h candles from them.
- **Market closed** Friday 22:00 to Sunday 21:00 UTC: gold / forex is skipped.
- **Strategy** (15m entries, `bot/fx_signals.py`): trade only with the 1h trend (price and EMA50 above EMA200 =
  LONG only, below = SHORT only). Entry = close beyond the last 20 candles' high / low, or a pullback to EMA20 that
  closes back in the trend direction. RSI 50-70 for LONG, 30-50 for SHORT; skipped when price is more than 2 ATR
  from EMA20. Stop 1.5 x the 15m ATR, take profit 1.5R. **Expires after 4 hours** if neither is hit.
- **Same quality filters as crypto**: one Claude review for all new candidates per run; sent only when trend, news
  and Claude all agree (CAUTION = no signal), HIGH confidence only by default (`FX_SEND_CONFIDENCE`), max 1 per run
  (highest ADX) and 3 per day. Held-back signals are logged quietly and show in `/stats` under the filters.
- **News check**: + = the pair's price goes UP, - = DOWN (a stronger yen = USD/JPY DOWN, a stronger dollar = gold DOWN).
- **Big news hold**: no signal within 60 minutes before a high-impact ForexFactory event (Fed, CPI, NFP) in one of the
  pair's currencies. The calendar is cached in `state.json` (downloaded again every 6 hours).
- **Leverage plans**: the same 10x-20x ... 60x-100x plans as crypto, stops kept inside liquidation.
- **Daily bias** at 6:00 Phnom Penh time (`FX_DAILY_HOUR_LOCAL`): LONG / SHORT / WAIT per pair from daily candles
  (news against the trend = WAIT), key levels (yesterday's high / low, pivot, R1 / S1, 20-day range) and today's big
  news, with one Claude call for all pairs. A bias, not a tracked signal.
- **`/stats`** shows crypto and gold / forex results apart, with the average time to TP or stop.

Settings: `bot/config.py`, section 12. The GitHub workflow is unchanged and runs crypto only.

### Run it on your laptop

1. Put your secrets in a `.env` file in the project folder (it is in `.gitignore`, never commit it):
   ```
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
   CLAUDE_CODE_OAUTH_TOKEN=...
   TWELVEDATA_API_KEY=...
   ```
   The bot reads it itself (values already set in your environment win). `ANTHROPIC_API_KEY` is always ignored.
2. `python -m bot loop` runs crypto and gold / forex every 5 minutes and answers `/stats` within seconds.
   A failed run is logged with the error and the loop keeps going. Stop it with **Ctrl+C**.
3. Run only one copy at a time (the loop OR the GitHub workflow answering Telegram), or they take each other's messages.

