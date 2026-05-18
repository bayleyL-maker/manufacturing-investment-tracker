"""
Step 1: RSS reader.

Fetches the configured RSS feed and prints each entry's title, date, and URL.
No LLM calls, no scraping - just confirms the feed works and shows what's in it.

Run with:  python scripts/fetch_feed.py
"""
import feedparser

FEED_URL = "https://www.manufacturingdive.com/feeds/news/"

def main():
    print(f"Fetching: {FEED_URL}\n")
    feed = feedparser.parse(FEED_URL)

    if feed.bozo:
        # bozo is feedparser's flag for "something looked off about this feed"
        print(f"Warning: feed parser reported an issue: {feed.bozo_exception}")

    entries = feed.entries
    print(f"Feed title: {feed.feed.get('title', '(unknown)')}")
    print(f"Found {len(entries)} entries.\n")

    for i, entry in enumerate(entries, 1):
        title = entry.get("title", "(no title)")
        link = entry.get("link", "(no link)")
        published = entry.get("published", entry.get("updated", "(no date)"))
        print(f"{i:>3}. {title}")
        print(f"     {published}")
        print(f"     {link}\n")

if __name__ == "__main__":
    main()
