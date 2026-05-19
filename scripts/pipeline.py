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
from datetime import datetime, timezone
from pathlib import Path

import feedparser

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

    counts = {"new": 0, "skipped_seen": 0, "skipped_not_relevant": 0,
              "skipped_dup": 0, "errors": 0, "added": 0}

    # Build the list of (entry, publication) tuples across all enabled feeds
    all_entries = []
    for feed_cfg in enabled_feeds:
        name = feed_cfg["name"]
        url = feed_cfg["url"]
        print(f"\nFetching feed: {name}  ({url})")
        feed = feedparser.parse(url)
        if feed.bozo:
            print(f"  warning: parser reported issue: {feed.bozo_exception}")
        print(f"  -> {len(feed.entries)} entries")
        for entry in feed.entries:
            all_entries.append((entry, name))

    print(f"\nTotal entries across all feeds: {len(all_entries)}\n")

    for entry, publication in all_entries:
        url = entry.link
        if url in seen:
            counts["skipped_seen"] += 1
            continue
        counts["new"] += 1

        title = entry.get("title", "(no title)")
        print(f"-> {title}")
        print(f"   {url}")

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
            seen.add(url)
            continue

        if not text:
            print(f"   ERROR: no text extracted")
            counts["errors"] += 1
            seen.add(url)
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
            seen.add(url)
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
            seen.add(url)
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
