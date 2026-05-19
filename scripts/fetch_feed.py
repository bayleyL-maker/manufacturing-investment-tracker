"""
RSS reader / feed inspector.

Fetches a feed and prints each entry's title, date, and URL. Useful for
testing a new feed URL before adding it to data/feeds.json.

Run with:
    python scripts/fetch_feed.py                            # uses default
    python scripts/fetch_feed.py <feed-url>                 # test a specific URL
"""
import sys
import feedparser

DEFAULT_FEED = "https://www.manufacturingdive.com/feeds/news/"

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FEED
    print(f"Fetching: {url}\n")
    feed = feedparser.parse(url)

    if feed.bozo:
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
