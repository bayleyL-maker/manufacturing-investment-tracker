"""
Step 6: Geocoding.

Scans pending.json and investments.json for records that are city-precise but
missing lat/lon. Queries Nominatim (OpenStreetMap's free geocoder) for each
unique city/state, caches results, and updates the records in place.

Run with:
    python scripts/geocode.py

Polite usage:
  - 1 request per second (Nominatim's rule)
  - real User-Agent including a contact email
  - cached results so the same city is never queried twice
"""
import json
import sys
import time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PENDING_PATH = DATA_DIR / "pending.json"
APPROVED_PATH = DATA_DIR / "investments.json"
CACHE_PATH = DATA_DIR / "geocode_cache.json"

# Replace the email below with your real one. Nominatim asks for a contact so
# they can reach you if your script misbehaves.
USER_AGENT = "us-mfg-investment-tracker/0.1 (contact: bayley.lackie@gmail.com)"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def geocode(city: str, state: str, cache: dict) -> tuple | None:
    """Return (lat, lon) or None. Uses + updates the cache."""
    key = f"{city.strip().lower()}|{state.strip().upper()}"
    if key in cache:
        hit = cache[key]
        return (hit["lat"], hit["lon"]) if hit else None

    params = {
        "q": f"{city}, {state}, USA",
        "format": "json",
        "limit": 1,
        "countrycodes": "us",
    }
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception as e:
        print(f"    geocode error for {city}, {state}: {e}")
        return None

    if not results:
        cache[key] = None
        return None

    lat = float(results[0]["lat"])
    lon = float(results[0]["lon"])
    cache[key] = {"lat": lat, "lon": lon}
    return (lat, lon)


def fill_coords(records: list, cache: dict) -> int:
    """Fill lat/lon in records that need it. Returns count of newly geocoded."""
    new_count = 0
    for r in records:
        loc = r.get("location", {})
        if loc.get("precision") != "city":
            continue
        if loc.get("lat") is not None and loc.get("lon") is not None:
            continue
        city = loc.get("city")
        state = loc.get("state")
        if not city or not state:
            continue

        key = f"{city.strip().lower()}|{state.strip().upper()}"
        cached = key in cache
        coords = geocode(city, state, cache)
        if coords:
            loc["lat"], loc["lon"] = coords
            print(f"    {city}, {state}  -> {coords[0]:.4f}, {coords[1]:.4f}" + ("  (cached)" if cached else ""))
            if not cached:
                new_count += 1
                time.sleep(1)  # Nominatim politeness
        else:
            print(f"    {city}, {state}  -> not found")
            if not cached:
                time.sleep(1)
    return new_count


def main():
    cache = load_json(CACHE_PATH, {})
    pending = load_json(PENDING_PATH, [])
    approved = load_json(APPROVED_PATH, [])

    if not pending and not approved:
        print("No records to geocode.")
        return

    print(f"Geocoding... (cache has {len(cache)} entries)\n")

    print("Pending records:")
    p_new = fill_coords(pending, cache)
    print(f"\nApproved records:")
    a_new = fill_coords(approved, cache)

    save_json(CACHE_PATH, cache)
    save_json(PENDING_PATH, pending)
    save_json(APPROVED_PATH, approved)

    print(f"\n--- done ---")
    print(f"  New cities geocoded this run: {p_new + a_new}")
    print(f"  Cache size now: {len(cache)} entries")


if __name__ == "__main__":
    main()
