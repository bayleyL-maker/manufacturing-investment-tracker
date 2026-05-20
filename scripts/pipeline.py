"""
Step 4: Pipeline wiring.

Runs the full daily ingestion:
  1. Fetch RSS feed
  2. Skip URLs already processed (cached in data/seen_urls.json)
  3. For each new article: fetch + clean + extract via Claude
  4. If relevant: write a record to data/pending.json (deduped against
     existing pending and approved records)
  5. Mark URL as seen regardless of outcome

Run with:
    python scripts/pipeline.py

This script DOES NOT modify data/investments.json. Approval happens in
the review step (step 5).
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
from urllib.parse import urlparse

try:
    from googlenewsdecoder import gnewsdecoder
except ImportError:
    gnewsdecoder = None  # handled at use site

# Reuse our helpers
sys.path.insert(0, os.path.dirname(__file__))
from fetch_article import fetch_and_clean  # noqa: E402
from extract import call_claude  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SEEN_PATH = DATA_DIR / "seen_urls.json"
PENDING_PATH = DATA_DIR / "pending.json"
APPROVED_PATH = DATA_DIR / "investments.json"
FEEDS_PATH = DATA_DIR / "feeds.json"

# Lightweight pre-filter for "us_only_filter" feeds. If the article's title +
# summary contains none of these terms, skip without paying for an LLM call.
US_HINTS = [
    "United States", "U.S.", " US ", " USA", "American",
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas",
    "Utah", "Vermont", "Virginia", "Washington", "West Virginia",
    "Wisconsin", "Wyoming",
]


def is_google_news_url(url: str) -> bool:
    try:
        return urlparse(url).netloc.endswith("news.google.com")
    except Exception:
        return False


def decode_google_news_url(url: str) -> str | None:
    """Resolve a news.google.com redirect to the real publisher URL.
    Returns None on failure."""
    if gnewsdecoder is None:
        return None
    try:
        res = gnewsdecoder(url, interval=1)
    except Exception as e:
        print(f"   GN decoder exception: {e}")
        return None
    if not res or not res.get("status"):
        msg = res.get("message", "no message") if res else "no result"
        print(f"   GN decode failed: {msg}")
        return None
    return res.get("decoded_url")


def looks_us_based(entry) -> bool:
    """Cheap keyword check: does title+summary mention any US state or US phrase?"""
    blob = " ".join([
        entry.get("title", ""),
        entry.get("summary", ""),
        entry.get("description", ""),
    ])
    return any(hint in blob for hint in US_HINTS)


def entry_age_days(entry) -> float | None:
    """Returns age in days, or None if no parseable date."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    pub = datetime(parsed.tm_year, parsed.tm_mon, parsed.tm_mday, tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - pub).total_seconds() / 86400.0

# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------
def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------
def slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s

def build_id(record: dict) -> str:
    """Stable ID derived from announced-date + company + location."""
    date = record["dates"]["announced"][:7]  # YYYY-MM
    company = slugify(record["company"]["name"])[:30]
    city = slugify(record["location"]["city"] or "")[:20]
    state = (record["location"].get("state") or "").lower()
    parts = [date, company]
    if city:
        parts.append(city)
    if state:
        parts.append(state)
    return "-".join(parts)

# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def main():
    feeds = load_json(FEEDS_PATH, [])
    enabled_feeds = [f for f in feeds if f.get("enabled", True)]
    if not enabled_feeds:
        print("No enabled feeds in data/feeds.json. Nothing to do.")
        return

    seen = set(load_json(SEEN_PATH, []))
    pending = load_json(PENDING_PATH, [])
    approved = load_json(APPROVED_PATH, [])

    existing_ids = {r["id"] for r in pending} | {r["id"] for r in approved}

    counts = {"new": 0, "skipped_seen": 0, "skipped_too_old": 0,
              "skipped_not_us": 0, "skipped_not_relevant": 0,
              "skipped_dup": 0, "errors": 0, "added": 0}

    # Build the list of (entry, publication) tuples across all enabled feeds,
    # applying us_only_filter and max_age_days where configured.
    all_entries = []
    for feed_cfg in enabled_feeds:
        name = feed_cfg["name"]
        url = feed_cfg["url"]
        us_only = feed_cfg.get("us_only_filter", False)
        max_age = feed_cfg.get("max_age_days")  # None = no limit
        print(f"\nFetching feed: {name}  ({url})")
        feed = feedparser.parse(url)
        if feed.bozo:
            print(f"  warning: parser reported issue: {feed.bozo_exception}")
        entries = feed.entries
        raw = len(entries)

        # Age filter
        if max_age is not None:
            kept = []
            for e in entries:
                age = entry_age_days(e)
                if age is None or age <= max_age:
                    kept.append(e)
            dropped = len(entries) - len(kept)
            counts["skipped_too_old"] += dropped
            entries = kept

        # US filter
        if us_only:
            kept = [e for e in entries if looks_us_based(e)]
            counts["skipped_not_us"] += len(entries) - len(kept)
            entries = kept

        print(f"  -> {raw} raw, {len(entries)} after filters")
        for entry in entries:
            all_entries.append((entry, name))

    print(f"\nTotal entries across all feeds (after filters): {len(all_entries)}\n")

    for entry, publication in all_entries:
        original_url = entry.link
        if original_url in seen:
            counts["skipped_seen"] += 1
            continue
        counts["new"] += 1

        title = entry.get("title", "(no title)")
        print(f"-> {title}")
        print(f"   {original_url}")

        # If this is a Google News redirect URL, decode to the real article URL
        if is_google_news_url(original_url):
            decoded = decode_google_news_url(original_url)
            if not decoded:
                counts["errors"] += 1
                seen.add(original_url)
                continue
            print(f"   -> decoded: {decoded}")
            if decoded in seen:
                print(f"   already seen (decoded URL)")
                counts["skipped_seen"] += 1
                seen.add(original_url)
                continue
            url = decoded
        else:
            url = original_url

        # date hint
        date_hint = None
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        if published:
            date_hint = f"{published.tm_year:04d}-{published.tm_mon:02d}-{published.tm_mday:02d}"

        # fetch + clean
        try:
            text = fetch_and_clean(url)
        except Exception as e:
            print(f"   ERROR fetching: {e}")
            counts["errors"] += 1
            seen.update({url, original_url})
            continue

        if not text:
            print(f"   ERROR: no text extracted")
            counts["errors"] += 1
            seen.update({url, original_url})
            continue

        # extract
        try:
            result = call_claude(text, date_hint)
        except Exception as e:
            print(f"   ERROR from Claude: {e}")
            counts["errors"] += 1
            # Don't mark as seen so we can retry tomorrow
            continue

        if not result.get("relevant"):
            reason = result.get("reason", "(no reason)")
            print(f"   SKIP: {reason}")
            counts["skipped_not_relevant"] += 1
            seen.update({url, original_url})
            continue

        record = result["record"]

        # finalize record
        record["id"] = build_id(record)
        record["location"].setdefault("lat", None)
        record["location"].setdefault("lon", None)
        record["suppliers"] = []
        record["sources"] = [{
            "url": url,
            "publication": publication,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
        }]
        record["review"] = {"status": "pending", "reviewed_at": None}

        if record["id"] in existing_ids:
            # Dedup: same investment already known. Append source if not present.
            target_list = pending if any(r["id"] == record["id"] for r in pending) else approved
            for r in target_list:
                if r["id"] == record["id"]:
                    urls = {s["url"] for s in r.get("sources", [])}
                    if url not in urls:
                        r["sources"].append(record["sources"][0])
                    break
            print(f"   DUP of existing id: {record['id']} (source appended)")
            counts["skipped_dup"] += 1
            seen.update({url, original_url})
            continue

        pending.append(record)
        existing_ids.add(record["id"])
        counts["added"] += 1
        print(f"   ADDED as pending: {record['id']}")
        seen.add(url)

        # be polite
        time.sleep(1)

    save_json(SEEN_PATH, sorted(seen))
    save_json(PENDING_PATH, pending)
    save_json(APPROVED_PATH, approved)  # may have had a source appended

    print("\n--- summary ---")
    for k, v in counts.items():
        print(f"  {k:>22}: {v}")
    print(f"\nPending records awaiting review: {len(pending)}")
    print(f"Approved records:                {len(approved)}")


if __name__ == "__main__":
    main()
