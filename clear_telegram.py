"""
clear_telegram.py - Deletes old messages from your Telegram channel (one-off helper, not part of the bot).

    python clear_telegram.py            # deletes message ids 1..400 after you type "yes"
    python clear_telegram.py 600        # deletes message ids 1..600

Uses TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from the environment (run-local.ps1 loads them from .env).
The bot must be an admin of the channel with the "Delete messages" right.
Ids that do not exist are skipped quietly.
"""

import os
import sys
import time

import requests

token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
last_id = int(sys.argv[1]) if len(sys.argv) > 1 else 400

if not token or not chat_id:
    sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first (run-local.ps1 does this from .env).")

if input(f"Delete message ids 1..{last_id} in chat {chat_id}? This cannot be undone. Type yes: ").strip() != "yes":
    sys.exit("Cancelled.")

deleted, errors = 0, {}
for mid in range(1, last_id + 1):
    while True:
        try:
            resp = requests.post(f"https://api.telegram.org/bot{token}/deleteMessage",
                                 json={"chat_id": chat_id, "message_id": mid}, timeout=20)
        except requests.RequestException as exc:
            print(f"  id {mid}: network error ({exc}), retrying in 5s")
            time.sleep(5)
            continue
        if resp.status_code == 429:  # too fast: Telegram says how long to wait
            wait = resp.json().get("parameters", {}).get("retry_after", 5)
            print(f"  Telegram asks to slow down, waiting {wait}s ...")
            time.sleep(wait)
            continue
        break
    if resp.ok:
        deleted += 1
    else:
        reason = resp.json().get("description", resp.text[:80])
        errors[reason] = errors.get(reason, 0) + 1
    if mid % 50 == 0:
        print(f"checked {mid}/{last_id} ids, deleted {deleted} so far", flush=True)
print(f"Deleted {deleted} messages.")
for reason, count in errors.items():
    print(f"  skipped {count}: {reason}")
