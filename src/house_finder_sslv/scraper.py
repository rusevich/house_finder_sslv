"""SS.LV flat-rental scraper.

Fetches new rental adverts from ss.lv for a configured set of Riga districts,
filters them by price and age, and stores unseen ones in a small SQLite DB so
the same advert is never reported twice.

Only the Python standard library is used, so there is nothing to install.
"""

from __future__ import annotations

import re
import sqlite3
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

# --------------------------------------------------------------------------- #
# Configuration — edit these to change what gets scraped.                      #
# --------------------------------------------------------------------------- #

# Monthly rent range in EUR. Adverts outside [MIN_PRICE_EUR, MAX_PRICE_EUR]
# are ignored.
MIN_PRICE_EUR = 0
MAX_PRICE_EUR = 400

# Only report adverts published within this many days.
MAX_AGE_DAYS = 3

# Districts to watch. Keys are human-friendly names (used in messages),
# values are the ss.lv URL slugs.
DISTRICTS = {
    # Pārdaugava — the Ķīpsala ↔ Vienības gatve axis.
    "Āgenskalns": "agenskalns",
    "Šampēteris-Pļeskodāle": "shampeteris-pleskodale",
    "Imanta": "imanta",
    "Torņakalns": "tornjakalns",
    "Zasulauks": "zasulauks",
    "Dzegužkalns": "dzeguzhkalns",
    "Iļģuciems": "ilguciems",
    "Ziepniekkalns": "ziepniekkalns",
    "Bieriņi": "bierini",
    "Zolitūde": "zolitude",
    "Bieķēnsala": "biekensala",
    # Centre — pricier, but well connected to both.
    "Centrs": "centre",
    "Ķīpsala": "kipsala",
    "Klīversala": "kliversala",
    "Vecrīga": "vecriga",
}

# Where the SQLite database lives (project root by default).
DB_PATH = Path(__file__).resolve().parents[2] / "listings.db"

# --------------------------------------------------------------------------- #
# Internals                                                                    #
# --------------------------------------------------------------------------- #

# "flats" -> rentals ("hand_over") RSS feed for a single Riga district.
_FEED_URL = "https://www.ss.lv/lv/real-estate/flats/riga/{slug}/hand_over/rss/"

# Pause between district feeds so a run doesn't hammer ss.lv with one burst.
_FETCH_DELAY_SECONDS = 1.0

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Matches e.g. "Cena: <b><b>400</b>  €/mēn.</b>" (tags already stripped:
# "Cena: 400  €/mēn.").
_PRICE_RE = re.compile(r"Cena:\s*([\d\s]+?)\s*€/mēn", re.IGNORECASE)
# Matches the room count, e.g. "Ist.: <b>2</b>" -> "Ist.: 2".
_ROOMS_RE = re.compile(r"Ist\.:\s*(\d+)", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class Listing:
    """A single rental advert."""

    url: str
    title: str
    price: int
    rooms: int | None
    district: str
    published: datetime


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub(" ", html)


def _parse_price(description: str) -> int | None:
    """Pull the monthly rent (EUR) out of an RSS <description> block."""
    match = _PRICE_RE.search(_strip_tags(description))
    if not match:
        return None
    digits = match.group(1).replace(" ", "")
    return int(digits) if digits.isdigit() else None


def _parse_rooms(description: str) -> int | None:
    """Pull the number of rooms out of an RSS <description> block."""
    match = _ROOMS_RE.search(_strip_tags(description))
    return int(match.group(1)) if match else None


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _parse_feed(raw: bytes, district: str) -> list[Listing]:
    """Turn one district's RSS payload into Listing objects."""
    root = ElementTree.fromstring(raw)
    listings: list[Listing] = []
    for item in root.iterfind("./channel/item"):
        link = item.findtext("link")
        title = (item.findtext("title") or "").strip()
        description = item.findtext("description") or ""
        pub_raw = item.findtext("pubDate")
        if not link or not pub_raw:
            continue
        price = _parse_price(description)
        if price is None:
            continue
        rooms = _parse_rooms(description)
        try:
            published = parsedate_to_datetime(pub_raw)
        except (TypeError, ValueError):
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        listings.append(
            Listing(
                url=link.strip(),
                title=title,
                price=price,
                rooms=rooms,
                district=district,
                published=published,
            )
        )
    return listings


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            url        TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            price      INTEGER NOT NULL,
            rooms      INTEGER,
            district   TEXT NOT NULL,
            published  TEXT NOT NULL,
            first_seen TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _is_new(conn: sqlite3.Connection, url: str) -> bool:
    row = conn.execute("SELECT 1 FROM listings WHERE url = ?", (url,)).fetchone()
    return row is None


def _save(conn: sqlite3.Connection, listing: Listing) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO listings
            (url, title, price, rooms, district, published, first_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            listing.url,
            listing.title,
            listing.price,
            listing.rooms,
            listing.district,
            listing.published.isoformat(),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def scrape(db_path: Path = DB_PATH) -> list[Listing]:
    """Fetch all watched districts and return only the newly seen adverts.

    Adverts are filtered by price and age, de-duplicated against the SQLite
    database, and any new ones are inserted before being returned. Running
    this repeatedly only ever yields adverts not seen on previous runs.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - MAX_AGE_DAYS * 86400
    new_listings: list[Listing] = []

    with sqlite3.connect(db_path) as conn:
        _init_db(conn)
        for index, (name, slug) in enumerate(DISTRICTS.items()):
            if index:
                time.sleep(_FETCH_DELAY_SECONDS)
            try:
                raw = _fetch(_FEED_URL.format(slug=slug))
            except Exception as exc:  # network/HTTP issues shouldn't kill the run
                print(f"[warn] failed to fetch {name}: {exc}")
                continue

            for listing in _parse_feed(raw, name):
                if not (MIN_PRICE_EUR <= listing.price <= MAX_PRICE_EUR):
                    continue
                if listing.published.timestamp() < cutoff:
                    continue
                if not _is_new(conn, listing.url):
                    continue
                _save(conn, listing)
                new_listings.append(listing)

    return new_listings


def main() -> None:
    new_listings = scrape()
    if not new_listings:
        print("No new listings.")
        return
    print(f"Found {len(new_listings)} new listing(s):\n")
    for listing in new_listings:
        rooms = f"{listing.rooms}-room" if listing.rooms else "? rooms"
        print(f"  [{listing.district}] {listing.price} €/mēn. · {rooms}")
        print(f"  {listing.title}")
        print(f"  {listing.url}")
        print(f"  published: {listing.published:%Y-%m-%d %H:%M}\n")


if __name__ == "__main__":
    main()
