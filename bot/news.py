"""
news.py - Collects recent crypto headlines from free RSS feeds.

Only headlines (titles) are used, never full articles. This keeps the
question to Claude short, which saves your Pro usage.
"""

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

HEADERS = {"User-Agent": "crypto-signal-bot/1.0"}


def _parse_date(text):
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def parse_feed(xml_text):
    """Return a list of (title, published_datetime_or_None) from RSS or Atom XML."""
    items = []
    root = ET.fromstring(xml_text)
    for node in root.iter():
        tag = node.tag.split("}")[-1]  # remove XML namespace
        if tag not in ("item", "entry"):
            continue
        title, published = None, None
        for child in node:
            ctag = child.tag.split("}")[-1]
            if ctag == "title" and child.text:
                title = " ".join(html.unescape(child.text).split())
            elif ctag in ("pubDate", "published", "updated") and published is None:
                published = _parse_date(child.text)
        if title:
            items.append((title, published))
    return items


def fetch_all_headlines(feeds, max_age_hours=48):
    """Download every feed once. Returns a list of unique, recent headlines."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    seen, results = set(), []
    for url in feeds:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            for title, published in parse_feed(resp.content):
                if published and published < cutoff:
                    continue
                key = title.lower()
                if key not in seen:
                    seen.add(key)
                    results.append(title)
        except Exception as exc:
            print(f"[news] could not read {url}: {exc}")
    return results


def filter_for_coin(headlines, keywords, limit):
    """Keep headlines that mention the coin (whole words only, so 'sol' != 'solution')."""
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, keywords)) + r")\b", re.IGNORECASE)
    return [h for h in headlines if pattern.search(h)][:limit]
