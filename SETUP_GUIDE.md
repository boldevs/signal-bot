# Crypto Signal Bot – Setup Guide

Oct 18, 2026

## Overview

The bot sends LONG / SHORT / WAIT alerts for BTC, ETH and SOL to your Telegram channel, runs free on GitHub Actions, and costs $0 beyond your Claude Pro plan.

- **Python is the watchman.** Every 5 minutes it downloads 15-minute candles and checks EMA20, EMA50 and RSI. No Claude is used here.
- **Claude is the expert.** Only when Python finds a new LONG or SHORT, the bot reads crypto headlines and asks Claude (your Pro login, via Claude Code) if the news is bullish, bearish or neutral.
- **VADER is the spare tire.** If Claude is unavailable (usage limit, error, timeout), a free offline analyzer answers instead, so you never miss an alert.
- **Telegram gets the alert** with price, trend, RSI, news verdict, stop loss, target and an approximate liquidation price.

How one check runs:

```
Every 5 minutes -> Python: EMA + RSI -> New signal?
                                           |-- no  -> Do nothing
                                           |-- yes -> Read headlines -> Ask Claude (Pro)
                                                                          |-- verdict        -> Telegram alert
                                                                          |-- limit or error -> VADER backup -> Telegram alert
```

Most checks end at "Do nothing". Claude is asked only after a new signal, and VADER answers only if Claude cannot.

Version 1 sends alerts only. It never places trades. Use it for testnet and paper trading while you learn. This is an educational project, not financial advice.

Example alert:

```
🟢 LONG SIGNAL: ETH (1h)
Price: $3,420.00
Trend: UP | RSI: 42.1
Setup: Up-trend + RSI recovered above 40
News: 😊 BULLISH (Claude)
Why: Strong ETF inflows and institutional adoption.
✅ News agrees with the signal
🛑 Stop loss: $3,330.00
🎯 Target: $3,600.00
💥 Approx. liquidation at 2x: $1,710.00
⚠️ Testnet / paper trade only. Not financial advice.
```

## What's in the project

It is a small bot: 9 Python files plus one GitHub Actions file, every part commented.

| File | What it does |
| --- | --- |
| `bot/config.py` | All settings: coins, indicators, risk, news feeds, Claude model. Edit here. |
| `bot/market_data.py` | Downloads closed 1-hour candles from Binance public data (Kraken as backup) |
| `bot/indicators.py` | Calculates EMA, RSI and ATR (same method as TradingView) |
| `bot/signals.py` | The strategy rules: LONG / SHORT / WAIT, stop loss, target |
| `bot/news.py` | Reads recent headlines from CoinDesk, Cointelegraph and Decrypt RSS feeds |
| `bot/sentiment.py` | Asks Claude about the news, with VADER as backup |
| `bot/telegram.py` | Builds and sends the alert and the daily summary |
| `bot/state.py` | Memory (`state.json`) so you get no duplicate alerts |
| `bot/__main__.py` | Main program and commands |
| `.github/workflows/signal-bot.yml` | Runs the bot on GitHub every 5 minutes |
| `tests/test_bot.py` | 19 offline tests |
| `requirements.txt` | Python packages: pandas, requests, vaderSentiment |

## Step 1: Install the tools on Windows (about 15 minutes)

You need Python, Git and Claude Code on your laptop once, to test and to create the Claude token. After that the bot runs on GitHub, not your laptop.

1. **Python:** download it from [python.org/downloads](https://www.python.org/downloads/). In the installer, tick **Add Python to PATH**, then click Install.
2. **Git for Windows:** download it from [git-scm.com/downloads/win](https://git-scm.com/downloads/win) and install with the default options.
3. **Claude Code:** open **PowerShell** (Start menu, type PowerShell) and run:

   ```
   irm https://claude.ai/install.ps1 | iex
   ```
4. Close PowerShell and open a new one. Check everything works:

   ```
   python --version
   git --version
   claude --version
   ```
5. **Log in to Claude Code:** run `claude`, then log in with your Claude Pro account in the browser. Type `/exit` when done.
6. **Install the bot's packages:** open PowerShell in the `crypto-signal-bot` folder, and run:

   ```
   pip install -r requirements.txt
   ```

Never set an `ANTHROPIC_API_KEY` on your computer. If one exists, Claude Code uses the paid API instead of your Pro plan. The bot also removes it before calling Claude, as an extra safety. Source: [Claude Code setup](https://code.claude.com/docs/en/setup).

## Step 2: Create the Telegram bot and channel (about 5 minutes)

You need two values: a **bot token** and a **chat ID**. Both are free.

1. In Telegram, search for **@BotFather**, open it and send `/newbot`.
2. Pick a name and a username ending in `bot` (for example `my_signal_alert_bot`).
3. BotFather replies with a **token** like `123456789:AAH...`. This is your `TELEGRAM_BOT_TOKEN`. Keep it secret.
4. Create a **channel** (New Channel). Open the channel's settings, Administrators, Add Admin, and add your bot with permission to post messages.
5. Get the **chat ID**:
   - Public channel: the ID is its username, for example `@my_signal_channel`.
   - Private channel: post any message in the channel, then open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in your browser. Find `"chat":{"id":-100...`. That number (with the minus sign) is the ID.
6. Save both values somewhere safe. You will use them in Step 3 and Step 5.

## Step 3: Test on your laptop (about 10 minutes)

Run these in PowerShell inside the `crypto-signal-bot` folder. If all four work, the bot is ready for GitHub.

1. **Run the tests** (no internet needed). You should see `19 passed`:

   ```
   pip install pytest
   python -m pytest -q
   ```
2. **Dry run with real prices.** It prints alerts instead of sending them:

   ```
   python -m bot run --dry-run
   ```

   You will see one line per coin, like `BTC: WAIT | price 67420.50 | RSI 51.3`. Most of the time all three say WAIT. That is normal.
3. **Test Telegram.** Put your two values in this PowerShell window, then send a test message:

   ```
   $env:TELEGRAM_BOT_TOKEN = "your-bot-token"
   $env:TELEGRAM_CHAT_ID = "@my_signal_channel"
   python -m bot test-telegram
   ```

   Check your channel for the test message.
4. **Test Claude.** It uses your Pro login from Step 1:

   ```
   python -m bot test-claude
   ```

   Success looks like `Claude test: OK -> ('BULLISH', '...')`.

The `$env:` values last only for this PowerShell window. On GitHub they are stored as secrets instead.

## Step 4: Create the Claude token (about 2 minutes)

GitHub cannot open a browser to log in, so it uses a long-lived token tied to your Pro plan. The token lasts one year and bills your subscription, not the paid API ([Claude Code authentication docs](https://code.claude.com/docs/en/authentication)).

1. In PowerShell, run:

   ```
   claude setup-token
   ```
2. Approve access in the browser window that opens.
3. The token prints in PowerShell (it starts with `sk-ant-oat01-`). Copy it. It is not saved anywhere, so paste it into GitHub in Step 5 right away.

Rules for this token:

- It gives access to your Claude subscription. Never put it in code, chat or a public file. Only in GitHub Secrets.
- It expires after one year. When alerts start showing `VADER backup` every time, run `claude setup-token` again and replace the secret.
- Every Claude call uses your normal Pro usage limits, shared with your chats and coding.

## Step 5: Run it on GitHub Actions (about 15 minutes)

After this step the bot runs every 5 minutes on GitHub's computers, with your laptop off.

**5a. Create the repository**

1. Sign up or log in at [github.com](https://github.com).
2. Click **New repository**. Name it `crypto-signal-bot`.
3. Choose **Public** for unlimited free minutes. Your secrets stay hidden even in a public repo. Choose Private only if you want the code hidden; then change the schedule to every 30 minutes (see below).
4. Do not add a README. Click **Create repository**.

**5b. Upload the code** (PowerShell, inside the `crypto-signal-bot` folder; replace `YOUR-NAME`):

```
git init
git add .
git commit -m "First version of the signal bot"
git branch -M main
git remote add origin https://github.com/YOUR-NAME/crypto-signal-bot.git
git push -u origin main
```

Git may ask you to log in to GitHub in the browser the first time.

**5c. Add the three secrets**

In your repo: **Settings**, **Secrets and variables**, **Actions**, **New repository secret**. Add each one:

| Secret name | Value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | The token from BotFather (Step 2) |
| `TELEGRAM_CHAT_ID` | `@my_signal_channel` or the `-100...` number (Step 2) |
| `CLAUDE_CODE_OAUTH_TOKEN` | The `sk-ant-oat01-...` token (Step 4) |
| `TWELVEDATA_API_KEY` | Free key from twelvedata.com (gold / forex candles; without it the Yahoo backup is used) |

Never add `ANTHROPIC_API_KEY`. That would bill the paid API.

**5d. Allow the bot to save its memory**

**Settings**, **Actions**, **General**, **Workflow permissions**: pick **Read and write permissions**, then **Save**.

**5e. Start it**

1. Open the **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
2. Click **Crypto Signal Bot**, then **Run workflow**. Wait about 1 minute.
3. A green tick means it worked. Click the run, then **Step 1 - check prices**, to see each coin's status.
4. You should get a **daily summary** in Telegram on the first run (if it is already past 08:00 UTC+7), then once a day after that. It proves the whole chain works.

From now on GitHub runs it every 5 minutes by itself. Runs can start a few minutes late at busy times.

**Private repo?** Open `.github/workflows/signal-bot.yml` and change `*/10 * * * *` to `*/30 * * * *` to stay inside the free monthly minutes.

## The strategy and how to change it

The stop loss sits just beyond the recent swing low (LONG) or swing high (SHORT), limited to 1-2 ATR from the price. The target is 2x the stop distance.

The bot checks only the last **closed** 1-hour candle, so a signal never appears and then disappears mid-candle. These rules are a simple, well-known starting point for learning, not a promise of profit.

| Signal | Needs RSI | And one of these |
| --- | --- | --- |
| LONG | below 70 (not overbought) | EMA20 crosses above EMA50, **or** trend is up (EMA20 > EMA50, price > EMA50) and RSI crosses up through 40 |
| SHORT | above 30 (not oversold) | EMA20 crosses below EMA50, **or** trend is down (EMA20 < EMA50, price < EMA50) and RSI crosses down through 60 |
| WAIT | any | none of the above |

Each LONG or SHORT alert includes:

- **Stop loss:** 1.5 × ATR away from the price (ATR = average candle size).
- **Target:** 3 × ATR away, so you aim to win 2× what you risk.
- **Approx. liquidation:** at your planned leverage (2× by default). It ignores exchange fees and margin rules, so the real one is a bit closer. Always set the stop loss well before it.

No spam: the same coin and side is not alerted again within 2 hours.

**Change settings** in `bot/config.py`, then push to GitHub (`git add .`, `git commit -m "new settings"`, `git push`):

| Setting | Default | What it changes |
| --- | --- | --- |
| `COINS` | BTC, ETH, SOL | Coins to watch, plus news keywords |
| `EMA_FAST`, `EMA_SLOW` | 20, 50 | Trend speed |
| `RSI_PULLBACK_LONG`, `RSI_PULLBACK_SHORT` | 40, 60 | Dip and bounce levels |
| `SWING_LOOKBACK`, `MIN_STOP_ATR`, `MAX_STOP_ATR` | 10, 1.0, 2.0 | Stop goes beyond the recent swing low/high, kept between 1 and 2 ATR |
| `REWARD_RISK` | 1.5 | Target distance = stop distance x 1.5 |
| `LEVERAGE` | 2 | Only for the liquidation warning |
| `MAX_HEADLINES` | 12 | Headlines sent to Claude (fewer = less Pro usage) |
| `USE_CLAUDE` | True | False = always use the free VADER backup |
| `CLAUDE_MODEL` | haiku | Claude model for news |
| `DAILY_SUMMARY_HOUR_UTC` | 1 | Summary time (1 = 08:00 UTC+7); None turns it off |

## Safety, costs and troubleshooting

**Costs: $0 beyond Claude Pro.** Price data, news feeds, Telegram, VADER and GitHub Actions (public repo) are free. Claude runs only on a new signal, usually 0–3 times a day, with a small model, no tools and a tiny instruction, so Pro usage stays light.

**Safety rules**

- Testnet or paper trade for at least 1–3 months before any real money.
- Leverage 1–3× maximum, risk 1–2% of your account per trade, always a stop loss on the exchange.
- Keep tokens only in GitHub Secrets. If a token leaks, revoke it: BotFather `/revoke` for Telegram, a new `claude setup-token` for Claude.
- Never add `ANTHROPIC_API_KEY` anywhere.

**Troubleshooting**

| Problem | Fix |
| --- | --- |
| Red X on a GitHub run | Open the run and read the failed step's log. `could not get prices` = data sites were down; the next run usually works. |
| No Telegram messages | Check the secret names are exact, the bot is an **admin** of the channel, and the chat ID is right. Run `python -m bot test-telegram` on your laptop. |
| Alerts always say `VADER backup` | The Claude token expired or Pro limit was reached. Run `claude setup-token`, update `CLAUDE_CODE_OAUTH_TOKEN`. |
| `git push` is rejected | The bot saved its memory first. Run `git pull --rebase`, then `git push`. |
| Only WAIT for days | Normal on 1-hour candles in a quiet market. Check the daily summary arrives, so you know it runs. |
| Bot stopped running after weeks | GitHub pauses schedules in inactive repos. The daily summary keeps it active; if paused, click **Enable workflow** in the Actions tab. |
| `claude` not found on Windows | Open a new PowerShell window. Still missing: re-run the install command from Step 1. |

## Next steps

- [ ] Finish Steps 1–5 and receive the first daily summary
- [ ] Paper trade every alert for 1–3 months and log each result in a trading journal
- [ ] Version 2: a backtest of the strategy on several years of data, with fees included
- [ ] Version 3: place practice orders automatically on the Binance Futures testnet
- [ ] Later: move to Oracle Cloud Always Free for exact 15-minute timing and FinBERT
