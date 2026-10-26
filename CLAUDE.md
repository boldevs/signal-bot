# CLAUDE.md

Crypto signal bot: sends LONG / SHORT / WAIT alerts for the coins in `config.COINS` (15m candles, only with the 1h trend; day trades that expire after 4h) to Telegram, plus gold and forex (XAU/USD, EUR/USD, GBP/USD, USD/JPY: 15m entries with the 1h trend, same quality filters, 4h expiry, morning daily bias). Alerts only — it never places trades. Educational project, not financial advice.

## Commands

```
pip install -r requirements.txt
python -m bot run --dry-run     # full check, prints alerts instead of sending
python -m bot run               # full check + send to Telegram (crypto + gold/forex)
python -m bot fx --dry-run      # gold/forex only
python -m bot loop              # laptop: run every 5 min until Ctrl+C (a failed run is logged, loop keeps going)
python -m bot test-telegram     # send a test message
python -m bot test-claude       # check Claude Code works with the Pro login
python -m pytest -q             # offline tests
```

## Layout

- `bot/config.py` — all settings (coins, indicators, risk, news feeds). Change settings here, not inline.
- `bot/market_data.py` — candles (Binance public data, Kraken fallback)
- `bot/indicators.py` — EMA, RSI, ATR
- `bot/signals.py` — strategy rules, plus `grade()` (HIGH / MEDIUM: trend, news and Claude must agree) and grouping of same-direction coins
- `bot/news.py` — RSS headlines
- `bot/events.py` — keyword rules for crypto event headlines (listings, hacks, ...), confirmed by one Claude call, with a trade idea
- `bot/macro.py` — world-news candidates (Fed, tariffs, war) judged by Claude in one call; Claude also names the coins (any in `ALL_COINS`)
- `bot/leverage.py` — one plan per leverage range (10x-20x ... 60x-100x), stops kept inside liquidation
- `bot/sentiment.py` — Claude news sentiment, VADER fallback
- `bot/telegram.py` — alert formatting and sending
- `bot/state.py` — dedupe memory so alerts aren't repeated
- `bot/tracker.py` — records each signal in `history.json`, judges WIN / LOSS / EXPIRED, builds `/stats` (see `GLOSSARY.md`)
- `bot/fx.py` — gold/forex run: candidates → one Claude review → `signals.grade()` → max 1 per run / 3 per day (rest logged as skipped), daily bias at 6:00 local, judging open FX signals
- `bot/fx_data.py` — FX candles (Twelve Data, Yahoo fallback), market hours; only 15m + 1d are downloaded (1h built from 15m) to fit the free 8/min, 800/day limit
- `bot/fx_signals.py` — FX strategy (1h EMA50/EMA200 trend, 20-candle breakout or EMA20 pullback, RSI/ATR filters, stop 1.5 ATR, TP 1.5R)
- `bot/fx_news.py` — one Claude call for all pairs; sign is the PAIR price (+ up, stronger yen = USD/JPY down); VADER fallback
- `bot/fx_calendar.py` — ForexFactory big news (cached in `state.json`); signals held as WAIT 60 min before
- `bot/__main__.py` — entry point / CLI, `.env` loader, `loop`
- `.github/workflows/signal-bot.yml` — runs every 5 min on GitHub Actions (crypto + gold/FX in one job)
- `tests/test_bot.py` — offline tests (no network); gold/forex tests are in `tests/test_fx.py`

## Rules

- Never add or use `ANTHROPIC_API_KEY` — it bills the paid API. Claude runs via Claude Code with `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`).
- Secrets (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CLAUDE_CODE_OAUTH_TOKEN`, `TWELVEDATA_API_KEY`) live in GitHub secrets / env or a local `.env` (gitignored) only; never commit them. The Twelve Data key is sent in a header, never in a URL (errors print URLs).
- Gold/forex runs only from `run`, `fx` and `loop` on the laptop. Leave `.github/workflows/signal-bot.yml` as it is (it runs crypto `detect` / `notify` only).
- Claude is called only when a signal appears (one review per run: the strongest coin), new world-news candidates exist, or new event candidates exist (one call per run each), or a coin button is tapped (one news call per tapped coin; leverage buttons never call Claude). Gold/forex: one review per run for all new FX candidates, and one call for the morning daily bias. If unavailable, button looks fall back to VADER (keep that working); a signal without a Claude review is not sent (logged as skipped); macro and event alerts are skipped and retried next run.
- Never add order placement or trading logic.
- Claude is called with the question on stdin and from a temp folder (Windows cuts argument at line breaks; the project's files must not be loaded). Keep it that way.
- `state.json` is the bot's memory and is committed by the workflow. Delete the local one before the first push so test runs don't hide real alerts.
- Tests must stay offline; mock network calls.
- Run `python -m pytest -q` and a `--dry-run` after changing strategy or sentiment code.

See `README.md` and `SETUP_GUIDE.md` for setup details.
