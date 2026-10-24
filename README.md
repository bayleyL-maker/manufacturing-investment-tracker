# US Manufacturing Investment Tracker

A webpage and ingestion pipeline that tracks announced manufacturing investments on US soil. It automatically reads RSS feeds, uses an LLM to extract structured records from relevant articles, and renders them as pins on an interactive US map.

Live site: GitHub Pages at the repo's Pages URL.

---

## Status

**v1 is live and working.** The core loop is complete end-to-end:

- Interactive dark-themed US map with Leaflet
- Pin clustering, hover tooltips, click-to-open sidebar
- Four filters: industry, investment type, dollar bucket, announced date
- Pins styled by location precision (solid = city, hollow = state-level)
- Multi-state investments (no single site) listed in a panel over the map
- Automated daily ingestion from multiple RSS feeds via Google News, Manufacturing Dive, and Area Development
- Google News URL decoder (resolves redirects to real publisher URLs)
- LLM-based article triage and structured extraction (Claude Haiku)
- Geocoding via Nominatim (OpenStreetMap) with caching
- Age filters (drops Google News articles older than 60 days, and any record announced more than 180 days ago)
- Record validation (malformed LLM output is retried up to 3 times, then skipped)
- Previously rejected investments are not re-queued
- US-only pre-filter (drops obviously non-US articles before any LLM call)
- Human-in-the-loop review workflow (CLI script or desktop GUI)
- GitHub Pages hosting + GitHub Actions pipeline runner (currently manual-trigger only)

### Not currently used

- **Supplier matching** is implemented but disabled in the pipeline and UI to save LLM cost. The supplier category table and matcher script remain in the repo for easy re-enablement.

---

## Industries tracked (12)

1. Agriculture Machinery
2. Heavy Equipment
3. Food & Beverage Machinery (equipment makers)
4. Food & Beverage Products (actual food/drink makers)
5. Automotive (any powertrain, including light EVs / micromobility)
6. EV / Battery (cell/pack/module)
7. Non-Auto Transportation (rail, aerospace, marine, trucking, off-highway)
8. Pharma & Biotech
9. Metals & Primary Materials
10. Fabricated Metal Products
11. Electrical Equipment & Grid
12. Consumer Packaged Goods

---

## Daily workflow

1. Launch the admin GUI (double-click `admin.bat` or run `python scripts/admin.py`)
2. Click **Run Pipeline** — fetches feeds, extracts investments, geocodes locations
3. Review each pending record: Approve / Reject / Edit JSON
4. Copy the git-push commands from the footer and paste into a terminal to publish

The live site auto-rebuilds within ~2 minutes of pushing.

---

## Project structure

```
Sales Webpage/
├── admin.bat                  Double-click launcher for the admin GUI
├── index.html                 Public map page
├── app.js                     Frontend: map, filters, sidebar, pin rendering
├── styles.css                 Dark theme + layout
├── state-centers.js           State-code to lat/lon for hollow-pin fallback
├── requirements.txt           Python dependencies
├── README.md                  This file
├── .env                       API key (gitignored, local only)
├── .github/workflows/
│   └── daily-ingest.yml       GitHub Actions pipeline runner
├── data/
│   ├── investments.json       Approved records (rendered on map)
│   ├── pending.json           Awaiting human review
│   ├── rejected.json          Audit trail
│   ├── seen_urls.json         URL cache (prevents re-processing)
│   ├── failed_urls.json       Retry counts for articles the LLM couldn't parse
│   ├── geocode_cache.json     City -> lat/lon cache
│   ├── feeds.json             RSS feed configuration
│   ├── supplier_categories.json  Curated supplier reference (currently unused)
│   ├── schema-example.json    Documented example record
│   └── demo-investments.json  Sample records for frontend testing
└── scripts/
    ├── admin.py               Desktop GUI (Tkinter)
    ├── run_daily.py           Wrapper: pipeline -> geocode
    ├── pipeline.py            RSS fetch, URL decode, filters, LLM extract
    ├── fetch_feed.py          RSS inspection utility
    ├── fetch_article.py       Article HTML -> clean text (trafilatura)
    ├── extract.py             LLM extraction (single article)
    ├── geocode.py             City/state -> lat/lon via Nominatim
    ├── records.py             Shared record validation + ID generation
    ├── match_suppliers.py     (disabled) LLM supplier matching
    ├── review.py              CLI review workflow (alternative to GUI)
    ├── unsee.py               Clear URLs from seen cache
    └── test_api.py            Anthropic API smoke test
```

---

## Tech stack

- **Frontend:** Plain HTML/CSS/JS + Leaflet (map) + Leaflet MarkerCluster. No frameworks or build step.
- **Map tiles:** Esri World Dark Gray Base (free, no API key)
- **Backend (ingestion):** Python 3.11
  - `feedparser` — RSS parsing
  - `requests` + `trafilatura` — article fetch + clean text extraction
  - `googlenewsdecoder` — resolves Google News redirect URLs
  - `anthropic` — Claude API client
- **LLM:** `claude-haiku-5-5` (effort `medium`) for article triage, extraction, and (previously) supplier matching
- **Geocoding:** Nominatim (OpenStreetMap, free, polite usage)
- **Hosting:** GitHub Pages (free, static)
- **Automation:** GitHub Actions (currently manual-trigger only)
- **Admin GUI:** Tkinter (built into Python, no additional install)

---

## Operational notes

### Cost

Roughly $0.10 per full pipeline run across all enabled feeds. Each run processes ~100–200 raw articles; filters drop most of them before any LLM call. Daily automation would cost <$5/month.

### GitHub Actions vs. local GUI

Both run `scripts/run_daily.py`. They do the same work. The GitHub cron is currently disabled; running via the GUI on your own machine is the primary workflow. Only enable the cron if you want unattended daily runs.

### Human-in-the-loop review

The pipeline writes new records only to `pending.json`. Nothing goes on the public map until you approve it in the review step. This prevents hallucinated or misclassified records from going live.

---

## Potential future improvements

- Pin colors or shapes per industry / investment type
- Investment amount proportional to pin size
- Timeline slider to scrub through time
- Additional RSS feeds (state economic development, trade pubs with hidden feeds)
- Expanded supplier category tables (if supplier matching is re-enabled)
- Mobile layout polish
- Web-based review interface (so review can happen without the local GUI)
- Per-industry queries in Google News for more targeted coverage
