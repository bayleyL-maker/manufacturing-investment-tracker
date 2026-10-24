"""
Step 4: Pipeline wiring.

Runs the full daily ingestion:
  1. Fetch RSS feed
  2. Skip URLs already processed (cached in data/seen_urls.json)
  3. For each new article: fetch + clean + extract via Claude
  4. If relevant: write a record to data/pending.json (deduped against
     existing pending, approved, and rejected records)
  5. Mark URL as seen regardless of outcome

Run with:
    python scripts/pipeline.py

This script DOES NOT add records to data/investments.json (it only appends
extra sources to records already there). Approval happens in the review step.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests
from urllib.parse import urlparse

try:
    from googlenewsdecoder import gnewsdecoder
except ImportError:
    gnewsdecoder = None  # handled at use site

# Reuse our helpers
sys.path.insert(0, os.path.dirname(__file__))
from fetch_article import fetch_and_clean, USER_AGENT  # noqa: E402
from extract import call_claude  # noqa: E402
from records import validate_record, build_id, announced_age_days  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SEEN_PATH = DATA_DIR / "seen_urls.json"
PENDING_PATH = DATA_DIR / "pending.json"
APPROVED_PATH = DATA_DIR / "investments.json"
REJECTED_PATH = DATA_DIR / "rejected.json"
FEEDS_PATH = DATA_DIR / "feeds.json"
FAILED_PATH = DATA_DIR / "failed_urls.json"

FEED_TIMEOUT = 30            # seconds; a hung feed must not stall the whole run
MAX_ANNOUNCED_AGE_DAYS = 180  # drop records announced longer ago than this (all feeds)
MAX_EXTRACT_FAILURES = 3     # give up on an article after this many unusable LLM replies
SAVE_EVERY = 10              # write progress to disk every N new articles

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

# Phrases that contain a US hint ("American") but point away from the US.
# Removed from the text before the hint check.
NON_US_PHRASES = ["Latin American", "South American", "Central American"]


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
    for phrase in NON_US_PHRASES:
        blob = blob.replace(phrase, "")
    return any(hint in blob for hint in US_HINTS)


def entry_age_days(entry) -> float | None:
    """Returns age in days, or None if no parseable date."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    pub = datetime(parsed.tm_year, parsed.tm_mon, parsed.tm_mday, tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - pub).total_seconds() / 86400.0


def fetch_feed(url: str):
    """Download with a timeout (feedparser.parse(url) has none), then parse."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=FEED_TIMEOUT)
    resp.raise_for_status()
    return feedparser.parse(resp.content)

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
# Run state
# ---------------------------------------------------------------------------
class Run:
    """Everything the pipeline reads and writes during one run."""

    def __init__(self):
        self.seen = set(load_json(SEEN_PATH, []))
        self.failed = load_json(FAILED_PATH, {})  # url -> count of unusable LLM replies
        self.pending = load_json(PENDING_PATH, [])
        self.approved = load_json(APPROVED_PATH, [])
        rejected = load_json(REJECTED_PATH, [])
        self.rejected_ids = {r["id"] for r in rejected}
        self.existing_ids = {r["id"] for r in self.pending} | {r["id"] for r in self.approved}
        self.counts = {"new": 0, "skipped_seen": 0, "skipped_too_old": 0,
                       "skipped_not_us": 0, "skipped_not_relevant": 0,
                       "skipped_dup": 0, "skipped_rejected": 0,
                       "errors": 0, "added": 0}

    def mark_seen(self, *urls):
        for u in urls:
            self.seen.add(u)
            self.failed.pop(u, None)

    def note_bad_reply(self, url: str, original_url: str):
        """Count an unusable LLM reply; stop retrying the article after a few."""
        n = self.failed.get(url, 0) + 1
        if n >= MAX_EXTRACT_FAILURES:
            print(f"   giving up after {n} unusable replies")
            self.mark_seen(url, original_url)
        else:
            self.failed[url] = n
            print(f"   will retry next run ({n}/{MAX_EXTRACT_FAILURES})")

    def save(self):
        save_json(SEEN_PATH, sorted(self.seen))
        save_json(FAILED_PATH, self.failed)
        save_json(PENDING_PATH, self.pending)
        save_json(APPROVED_PATH, self.approved)  # may have had a source appended

# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def collect_entries(enabled_feeds, counts) -> list:
    """Build the list of (entry, publication) tuples across all enabled feeds,
    applying us_only_filter and max_age_days where configured."""
    all_entries = []
    for feed_cfg in enabled_feeds:
        name = feed_cfg["name"]
        url = feed_cfg["url"]
        us_only = feed_cfg.get("us_only_filter", False)
        max_age = feed_cfg.get("max_age_days")  # None = no limit
        print(f"\nFetching feed: {name}  ({url})")
        try:
            feed = fetch_feed(url)
        except Exception as e:
            print(f"  ERROR fetching feed, skipping it: {e}")
            counts["errors"] += 1
            continue
        if feed.bozo:
            print(f"  warning: parser reported issue: {feed.bozo_exception}")
        entries = [e for e in feed.entries if e.get("link")]
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
    return all_entries


def process_entry(run: Run, entry, publication: str):
    counts = run.counts
    original_url = entry["link"]
    if original_url in run.seen:
        counts["skipped_seen"] += 1
        return
    counts["new"] += 1

    title = entry.get("title", "(no title)")
    print(f"-> {title}")
    print(f"   {original_url}")

    # If this is a Google News redirect URL, decode to the real article URL
    if is_google_news_url(original_url):
        decoded = decode_google_news_url(original_url)
        if not decoded:
            counts["errors"] += 1
            run.mark_seen(original_url)
            return
        print(f"   -> decoded: {decoded}")
        if decoded in run.seen:
            print(f"   already seen (decoded URL)")
            counts["skipped_seen"] += 1
            run.mark_seen(original_url)
            return
        url = decoded
    else:
        url = original_url

    # date hint
    date_hint = None
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        date_hint = f"{published.tm_year:04d}-{published.tm_mon:02d}-{published.tm_mday:02d}"

    # fetch + clean (fetch_and_clean returns None on any fetch error)
    text = fetch_and_clean(url)
    if not text:
        print(f"   ERROR: no text extracted")
        counts["errors"] += 1
        run.mark_seen(url, original_url)
        return

    # extract
    try:
        result = call_claude(text, date_hint)
    except ValueError as e:
        # Reply wasn't usable JSON. Retry a limited number of times.
        print(f"   ERROR: unusable reply from Claude: {e}")
        counts["errors"] += 1
        run.note_bad_reply(url, original_url)
        return
    except Exception as e:
        # API / network problem - not the article's fault, so retry next run.
        print(f"   ERROR from Claude: {e}")
        counts["errors"] += 1
        return

    if not result.get("relevant"):
        reason = result.get("reason", "(no reason)")
        print(f"   SKIP: {reason}")
        counts["skipped_not_relevant"] += 1
        run.mark_seen(url, original_url)
        return

    record = result.get("record")
    if isinstance(record, dict) and isinstance(record.get("location"), dict):
        loc = record["location"]
        if isinstance(loc.get("state"), str):
            loc["state"] = loc["state"].strip().upper()
    problem = validate_record(record)
    if problem:
        print(f"   ERROR: invalid record from Claude: {problem}")
        counts["errors"] += 1
        run.note_bad_reply(url, original_url)
        return

    age = announced_age_days(record)
    if age > MAX_ANNOUNCED_AGE_DAYS:
        print(f"   SKIP: announced {record['dates']['announced']} ({age} days ago)")
        counts["skipped_too_old"] += 1
        run.mark_seen(url, original_url)
        return

    # finalize record
    record["id"] = build_id(record)
    record["location"].setdefault("city", None)
    record["location"].setdefault("lat", None)
    record["location"].setdefault("lon", None)
    record["suppliers"] = []
    record["sources"] = [{
        "url": url,
        "publication": publication,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
    }]
    record["review"] = {"status": "pending", "reviewed_at": None}

    if record["id"] in run.rejected_ids:
        print(f"   SKIP: matches previously rejected id: {record['id']}")
        counts["skipped_rejected"] += 1
        run.mark_seen(url, original_url)
        return

    if record["id"] in run.existing_ids:
        # Dedup: same investment already known. Append source if not present.
        target_list = run.pending if any(r["id"] == record["id"] for r in run.pending) else run.approved
        for r in target_list:
            if r["id"] == record["id"]:
                urls = {s["url"] for s in r.get("sources", [])}
                if url not in urls:
                    r.setdefault("sources", []).append(record["sources"][0])
                break
        print(f"   DUP of existing id: {record['id']} (source appended)")
        counts["skipped_dup"] += 1
        run.mark_seen(url, original_url)
        return

    run.pending.append(record)
    run.existing_ids.add(record["id"])
    counts["added"] += 1
    print(f"   ADDED as pending: {record['id']}")
    run.mark_seen(url, original_url)

    # be polite
    time.sleep(1)


def main():
    # Never let an odd character in a headline crash the run when stdout is
    # a pipe with a narrow encoding (e.g. cp1252 under the admin GUI).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    feeds = load_json(FEEDS_PATH, [])
    enabled_feeds = [f for f in feeds if f.get("enabled", True)]
    if not enabled_feeds:
        print("No enabled feeds in data/feeds.json. Nothing to do.")
        return

    run = Run()
    all_entries = collect_entries(enabled_feeds, run.counts)
    print(f"\nTotal entries across all feeds (after filters): {len(all_entries)}\n")

    # Save in a finally block (and periodically) so a crash or Ctrl+C never
    # throws away the articles already processed and paid for.
    try:
        last_saved_at = 0
        for entry, publication in all_entries:
            try:
                process_entry(run, entry, publication)
            except Exception as e:
                print(f"   UNEXPECTED ERROR, skipping article: {e!r}")
                run.counts["errors"] += 1
            if run.counts["new"] - last_saved_at >= SAVE_EVERY:
                run.save()
                last_saved_at = run.counts["new"]
    finally:
        run.save()

    print("\n--- summary ---")
    for k, v in run.counts.items():
        print(f"  {k:>22}: {v}")
    print(f"\nPending records awaiting review: {len(run.pending)}")
    print(f"Approved records:                {len(run.approved)}")


if __name__ == "__main__":
    main()
