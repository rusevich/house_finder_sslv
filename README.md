# house-finder-sslv

Watches [ss.lv](https://www.ss.lv) for new rental flats in chosen Riga
districts and pushes them to your Telegram. Standard library only — nothing to
install beyond the package itself.

## How it works

- **`scraper.py`** — reads ss.lv's RSS feeds for rentals in the configured
  districts, keeps only adverts within the price range and recent enough, and
  records unseen ones in a SQLite DB (`listings.db`) so nothing is reported
  twice. Returns the new adverts.
- **`bot.py`** — runs the scraper every 10 minutes and sends each new advert to
  a Telegram chat via a bot.

## Configure what to watch

Edit the constants at the top of `src/house_finder_sslv/scraper.py`:

```python
MIN_PRICE_EUR = 0
MAX_PRICE_EUR = 400        # max monthly rent
MAX_AGE_DAYS  = 3          # ignore adverts older than this
DISTRICTS = {              # name -> ss.lv URL slug
    "Āgenskalns":            "agenskalns",
    "Šampēteris-Pļeskodāle": "shampeteris-pleskodale",
    "Imanta":                "imanta",
    "Torņakalns":            "tornjakalns",
    ...                      # 15 districts in total
}
```

Slugs come from the region list on ss.lv's rentals page
(<https://www.ss.lv/lv/real-estate/flats/riga/hand_over/>) — a wrong slug just
prints a `[warn] failed to fetch` line and is skipped.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Send your new bot any message.
3. `cp .env.example .env` and add your token.
4. Discover your chat id and add it to `.env`:

   ```sh
   uv run house-finder-sslv chatid
   ```

## Run

```sh
uv run house-finder-sslv        # poll every 10 min and notify (default)
uv run house-finder-sslv once   # one scrape + notify pass
```

The first launch seeds the database with current adverts silently, so you only
get notified about genuinely new ones afterwards.
