"""
Step 2: Article fetcher.

Downloads a single article URL and uses trafilatura to strip it down to clean
plain text (no nav, ads, footers, related-articles, etc.).

Run with:
    python scripts/fetch_article.py <url>

If no URL is given, it picks the first article from the configured RSS feed.
"""
import sys
import requests
import trafilatura
import feedparser

FEED_URL = "https://www.manufacturingdive.com/feeds/news/"

# Pretend to be a real browser so sites don't 403 us.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)


def fetch_and_clean(url: str) -> str | None:
    """Download URL, return clean article text. Returns None if extraction fails."""
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ERROR fetching {url}: {e}", file=sys.stderr)
        return None

    text = trafilatura.extract(
        resp.text,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    return text


def main():
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        # Default: first entry from the feed
        feed = feedparser.parse(FEED_URL)
        if not feed.entries:
            print("No entries in feed.")
            sys.exit(1)
        url = feed.entries[0].link
        print(f"(no URL given - using first feed entry)\n")

    print(f"URL: {url}\n")
    text = fetch_and_clean(url)
    if not text:
        print("Extraction returned no text. Site may block scrapers or use heavy JS.")
        sys.exit(1)

    print(f"Extracted {len(text)} characters of clean text.\n")
    print("--- BEGIN ARTICLE TEXT ---")
    print(text)
    print("--- END ARTICLE TEXT ---")


if __name__ == "__main__":
    main()
