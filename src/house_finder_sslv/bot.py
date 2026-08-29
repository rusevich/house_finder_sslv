"""Telegram notifier for new ss.lv rental adverts.

Runs the scraper on a fixed schedule and pushes any newly-found adverts to a
Telegram chat via a bot. Standard library only.

Setup
-----
1. Create a bot with @BotFather and copy the token.
2. Send your new bot any message (so it can see your chat).
3. Put your credentials in a `.env` file next to this project, or export them:

       TELEGRAM_BOT_TOKEN=123456:abcdef...
       TELEGRAM_CHAT_ID=987654321

   Don't know your chat id? Run:  python -m house_finder_sslv.bot chatid
   (after you've messaged the bot) and it will print it for you.

Usage
-----
    python -m house_finder_sslv.bot          # run the polling loop
    python -m house_finder_sslv.bot once     # scrape + notify a single time
    python -m house_finder_sslv.bot chatid   # discover your chat id
"""

from __future__ import annotations

import html
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .scraper import Listing, scrape

# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #

# How often to check for new adverts, in seconds.
POLL_INTERVAL_SECONDS = 10  # 10 seconds

# Small pause between messages so a burst doesn't hit Telegram's rate limit.
_SEND_DELAY_SECONDS = 0.2

_API = "https://api.telegram.org/bot{token}/{method}"
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


# --------------------------------------------------------------------------- #
# Credentials                                                                 #
# --------------------------------------------------------------------------- #

def _load_env() -> None:
    """Load KEY=VALUE lines from a local .env file into os.environ.

    Existing environment variables always win, so exporting still works.
    """
    if not _ENV_PATH.exists():
        return
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set (see .env instructions in bot.py).")
    return token


def _chat_id() -> str:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        sys.exit("TELEGRAM_CHAT_ID is not set (run: python -m house_finder_sslv.bot chatid).")
    return chat_id


# --------------------------------------------------------------------------- #
# Telegram API                                                                #
# --------------------------------------------------------------------------- #

def _call(token: str, method: str, params: dict) -> dict:
    url = _API.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode()
    request = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def send_message(token: str, chat_id: str, text: str) -> None:
    _call(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        },
    )


def format_listing(listing: Listing) -> str:
    rooms = f"{listing.rooms} ist." if listing.rooms else "? ist."
    return (
        f"🏠 <b>{html.escape(listing.district)}</b> · "
        f"{listing.price} €/mēn · {rooms}\n"
        f"{html.escape(listing.title)}\n"
        f'<a href="{html.escape(listing.url)}">Apskatīt sludinājumu →</a>'
    )


def get_chat_id(token: str) -> str | None:
    """Return the chat id of whoever last messaged the bot, if anyone has."""
    result = _call(token, "getUpdates", {})
    for update in reversed(result.get("result", [])):
        message = update.get("message") or update.get("channel_post")
        if message and "chat" in message:
            return str(message["chat"]["id"])
    return None


# --------------------------------------------------------------------------- #
# Runner                                                                       #
# --------------------------------------------------------------------------- #

def notify_new() -> int:
    """Scrape once and send every new advert to Telegram. Returns the count."""
    token, chat_id = _token(), _chat_id()
    listings = scrape()
    for listing in listings:
        send_message(token, chat_id, format_listing(listing))
        time.sleep(_SEND_DELAY_SECONDS)
    return len(listings)


def run() -> None:
    """Poll forever, notifying about new adverts every POLL_INTERVAL_SECONDS."""
    _token(), _chat_id()  # fail fast if credentials are missing

    # The first run has an empty database, so every current advert counts as
    # "new" and gets sent — you get the full current batch up front. After
    # that, only genuinely fresh adverts are notified.
    print(f"Polling every {POLL_INTERVAL_SECONDS}s. Ctrl-C to stop.")
    while True:
        try:
            count = notify_new()
            if count:
                print(f"Sent {count} new advert(s).")
        except Exception as exc:  # keep the loop alive through transient errors
            print(f"[warn] poll failed: {exc}")
        time.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    _load_env()
    command = sys.argv[1] if len(sys.argv) > 1 else "run"

    if command == "chatid":
        chat_id = get_chat_id(_token())
        if chat_id:
            print(f"Your chat id: {chat_id}")
            print("Add it to .env as TELEGRAM_CHAT_ID.")
        else:
            print("No messages found — send your bot a message first, then retry.")
    elif command == "once":
        print(f"Sent {notify_new()} new advert(s).")
    elif command == "run":
        run()
    else:
        sys.exit(f"Unknown command: {command!r} (use: run | once | chatid)")


if __name__ == "__main__":
    main()
