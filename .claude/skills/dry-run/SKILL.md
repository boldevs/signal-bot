---
name: dry-run
description: Run the signal bot's offline tests and a full dry-run check (no Telegram messages sent). Use after changing strategy, indicators, news, or sentiment code, or when the user asks to dry-run or verify the bot.
---

# Dry-run the signal bot

1. Run the offline tests:
   ```
   python -m pytest -q
   ```
   If any fail, stop and report the failures before continuing.

2. Run the full check without sending anything:
   ```
   python -m bot run --dry-run
   ```
   This prints alerts instead of sending them to Telegram.

3. Report briefly:
   - Test result (passed/failed counts).
   - For each coin (BTC, ETH, SOL): the signal (LONG / SHORT / WAIT) and, if shown, which sentiment source was used (Claude or VADER fallback).
   - Any errors or warnings (e.g. market data fallback to Kraken, Claude unavailable).

## Rules

- Never run `python -m bot run` without `--dry-run`, and never run `test-telegram`, unless the user explicitly asks — those send real messages.
- Do not set or use `ANTHROPIC_API_KEY`.
- If dependencies are missing, run `pip install -r requirements.txt` first.
