"""
Step 3: LLM extractor.

Takes an article URL (or uses the first feed entry), fetches and cleans the
text, then asks Claude to extract a structured investment record matching our
schema - or to mark the article as not relevant.

Run with:
    python scripts/extract.py [url]
"""
import json
import os
import sys
import re
from dotenv import load_dotenv
from anthropic import Anthropic

# Reuse our fetcher
sys.path.insert(0, os.path.dirname(__file__))
from fetch_article import fetch_and_clean, FEED_URL  # noqa: E402
import feedparser  # noqa: E402

load_dotenv()

MODEL = "claude-haiku-4-5"

EXTRACTION_PROMPT = """You are extracting structured data about US manufacturing investments from a news article.

We track investments in these 6 industries only:
- agriculture_machinery       (tractors, combines, sprayers, ag equipment)
- heavy_equipment             (construction, mining, earthmoving equipment)
- food_beverage_machinery     (food processing, packaging equipment)
- automotive                  (vehicle assembly plants, any powertrain)
- ev_battery                  (battery cell/pack/module manufacturing, battery components)
- non_auto_transportation     (rail, aerospace, marine, commercial trucking, off-highway)

An article is RELEVANT only if ALL of the following are true:
1. It describes a specific investment in a US-based manufacturing facility
2. The facility is or will be in one of the 6 industries above
3. The investment involves physical manufacturing on US soil (not just R&D, not just software, not just sales offices)
4. The investment type is one of: new_facility, expansion, equipment_upgrade, onshoring, automation, retooling, reopening

If the article is NOT relevant, respond with EXACTLY this JSON and nothing else:
{"relevant": false, "reason": "<short reason>"}

If the article IS relevant, respond with EXACTLY this JSON shape and nothing else:
{
  "relevant": true,
  "record": {
    "company": {"name": "<official company name>", "hq_country": "<country or null>"},
    "industry": "<one of the 6 values above>",
    "investment_type": "<one of: new_facility, expansion, equipment_upgrade, onshoring, automation, retooling, reopening>",
    "amount_usd": <integer dollars, or null if not disclosed>,
    "amount_disclosed": <true or false>,
    "location": {
      "city": "<city name or null>",
      "state": "<2-letter state code>",
      "precision": "<'city' if a city is named, otherwise 'state'>"
    },
    "dates": {
      "announced": "<YYYY-MM-DD of article date>",
      "expected_start": "<free text like '2026-Q3' or null>",
      "expected_completion": "<free text like '2028' or null>"
    },
    "description": "<1-2 sentence summary of the investment>"
  }
}

Rules:
- Return ONLY the JSON object. No markdown fences, no commentary before or after.
- amount_usd must be raw integer dollars (e.g., 7600000000 for $7.6 billion). Never include commas or currency symbols.
- If the article hints at a number like "billions" without a specific figure, set amount_usd to null and amount_disclosed to false.
- Use the article's publish date as 'announced'. If unsure, use today's date.
- If multiple US locations are mentioned, pick the primary one. Note others in the description if important.
- Do NOT populate suppliers, lat/lon, sources, or id - those are added by later pipeline steps.
"""


def extract_json(text: str) -> dict:
    """Pull a JSON object out of Claude's response, tolerating stray prose or fences."""
    # Strip code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    # Find the first { ... } block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object found in response: {text[:200]}")
    return json.loads(m.group(0))


def call_claude(article_text: str, article_date_hint: str | None) -> dict:
    client = Anthropic()
    user_content = f"Article publish date (use as 'announced' if relevant): {article_date_hint or 'unknown'}\n\nArticle text:\n\n{article_text}"
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=EXTRACTION_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = resp.content[0].text
    return extract_json(raw)


def main():
    # Get URL and (if from feed) a date hint
    date_hint = None
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        feed = feedparser.parse(FEED_URL)
        if not feed.entries:
            print("No entries in feed.")
            sys.exit(1)
        entry = feed.entries[0]
        url = entry.link
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        if published:
            date_hint = f"{published.tm_year:04d}-{published.tm_mon:02d}-{published.tm_mday:02d}"
        print(f"(no URL given - using first feed entry)\n")

    print(f"URL: {url}")
    print(f"Date hint: {date_hint}\n")

    text = fetch_and_clean(url)
    if not text:
        print("Could not extract article text.")
        sys.exit(1)
    print(f"Article text: {len(text)} chars\n")

    print(f"Asking {MODEL}...\n")
    result = call_claude(text, date_hint)

    print("--- RESULT ---")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
