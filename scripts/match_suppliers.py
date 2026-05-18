"""
Step 7: Supplier matching.

For each record that has no suppliers populated, asks Claude to pick the most
relevant component categories AND manufacturers from the curated table in
data/supplier_categories.json. The LLM is constrained to names that exist in
the table - it cannot invent new ones.

Run with:
    python scripts/match_suppliers.py

Updates both pending.json and investments.json in place.
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
from anthropic import Anthropic

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PENDING_PATH = DATA_DIR / "pending.json"
APPROVED_PATH = DATA_DIR / "investments.json"
CATEGORIES_PATH = DATA_DIR / "supplier_categories.json"

load_dotenv()
MODEL = "claude-haiku-4-5"


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


def extract_json(text: str):
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object found: {text[:200]}")
    return json.loads(m.group(0))


SYSTEM_PROMPT = """You are matching a US manufacturing investment to its likely Tier 1/2 equipment suppliers.

You will receive:
  1. A specific investment (company, industry, type, description).
  2. A catalog of component categories and the manufacturers in each, for that industry.

Your job: pick the 3 to 6 component categories from the catalog that are MOST relevant
to this specific investment, and for each, list 3 to 5 manufacturers from the catalog
that are most likely to supply that equipment.

HARD RULES:
- Use ONLY component_category names that appear in the catalog. Do not invent new ones.
- Use ONLY manufacturer names that appear in the catalog under that category. Do not invent new ones.
- Prefer categories the investment description strongly implies. If the description says
  "battery cell plant" pick coating/calendaring/etc, not generic robots alone.
- If the investment is small (equipment upgrade, automation) pick 2-4 narrowly-relevant categories.
- If the investment is a full new factory, pick a broader set (4-6 categories).

Respond with EXACTLY this JSON shape, nothing else:
{
  "suppliers": [
    {"component_category": "<name from catalog>", "candidates": ["<mfr>", "<mfr>", ...]},
    ...
  ]
}
"""


def match_suppliers(record: dict, catalog: dict, client) -> list:
    industry = record["industry"]
    industry_catalog = catalog.get(industry, {})
    if not industry_catalog:
        print(f"    no catalog entries for industry {industry}")
        return []

    user_msg = (
        f"Investment:\n"
        f"  Company: {record['company']['name']}\n"
        f"  Industry: {industry}\n"
        f"  Investment type: {record['investment_type']}\n"
        f"  Location: {record['location'].get('city')}, {record['location'].get('state')}\n"
        f"  Description: {record.get('description', '')}\n\n"
        f"Catalog (component_category -> manufacturers):\n"
        f"{json.dumps(industry_catalog, indent=2)}\n"
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    result = extract_json(resp.content[0].text)

    # Defensive filtering: drop any category/mfr not actually in the catalog
    cleaned = []
    for entry in result.get("suppliers", []):
        cat = entry.get("component_category")
        if cat not in industry_catalog:
            continue
        allowed = set(industry_catalog[cat])
        candidates = [c for c in entry.get("candidates", []) if c in allowed]
        if candidates:
            cleaned.append({"component_category": cat, "candidates": candidates})
    return cleaned


def fill_suppliers(records: list, catalog: dict, client) -> int:
    new_count = 0
    for r in records:
        if r.get("suppliers"):  # already populated
            continue
        print(f"  -> {r['company']['name']} ({r['industry']}) ...")
        try:
            suppliers = match_suppliers(r, catalog, client)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        r["suppliers"] = suppliers
        print(f"    matched {len(suppliers)} component categories")
        new_count += 1
        time.sleep(1)
    return new_count


def main():
    catalog = load_json(CATEGORIES_PATH, {})
    if not catalog:
        print("supplier_categories.json missing or empty.")
        sys.exit(1)

    pending = load_json(PENDING_PATH, [])
    approved = load_json(APPROVED_PATH, [])

    if not pending and not approved:
        print("No records to process.")
        return

    client = Anthropic()

    print("Pending records:")
    p_new = fill_suppliers(pending, catalog, client)
    print("\nApproved records:")
    a_new = fill_suppliers(approved, catalog, client)

    save_json(PENDING_PATH, pending)
    save_json(APPROVED_PATH, approved)

    print(f"\n--- done ---")
    print(f"  Records updated this run: {p_new + a_new}")


if __name__ == "__main__":
    main()
