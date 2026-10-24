"""
Shared record helpers: allowed enum values, validation, and ID generation.

Used by the pipeline (to reject malformed LLM output before it can crash the
run) and by the review tools (to reject hand-edits that would break the map).
"""
import re
import unicodedata
from datetime import date, datetime

VALID_INDUSTRIES = {
    "agriculture_machinery",
    "heavy_equipment",
    "food_beverage_machinery",
    "food_and_beverage",
    "automotive",
    "ev_battery",
    "non_auto_transportation",
    "pharma_biotech",
    "metals_and_primary_materials",
    "fabricated_metal_products",
    "electrical_equipment_and_grid",
    "consumer_packaged_goods",
}

VALID_TYPES = {
    "new_facility",
    "expansion",
    "equipment_upgrade",
    "onshoring",
    "automation",
    "retooling",
    "reopening",
}

# "multi_state" = the investment spans several states with no single primary
# site; state is null and the frontend lists it outside the map.
VALID_PRECISIONS = {"city", "state", "multi_state"}


def validate_record(record) -> str | None:
    """Return a short description of the first problem found, or None if OK."""
    if not isinstance(record, dict):
        return "record is not a JSON object"

    company = record.get("company")
    if not isinstance(company, dict) or not company.get("name"):
        return "missing company.name"

    if record.get("industry") not in VALID_INDUSTRIES:
        return f"invalid industry: {record.get('industry')!r}"
    if record.get("investment_type") not in VALID_TYPES:
        return f"invalid investment_type: {record.get('investment_type')!r}"

    amount = record.get("amount_usd")
    if amount is not None and (isinstance(amount, bool) or not isinstance(amount, int)):
        return f"amount_usd must be an integer or null: {amount!r}"

    loc = record.get("location")
    if not isinstance(loc, dict):
        return "missing location"
    if loc.get("precision") not in VALID_PRECISIONS:
        return f"invalid location.precision: {loc.get('precision')!r}"
    state = loc.get("state")
    if loc["precision"] == "multi_state":
        if state is not None:
            return "multi_state records must have state = null"
    elif not (isinstance(state, str) and len(state) == 2):
        return f"location.state must be a 2-letter code: {state!r}"
    if loc["precision"] == "city" and not loc.get("city"):
        return "precision is 'city' but location.city is empty"

    dates = record.get("dates")
    if not isinstance(dates, dict):
        return "missing dates"
    if parse_announced(dates.get("announced")) is None:
        return f"dates.announced must be YYYY-MM-DD: {dates.get('announced')!r}"

    return None


def parse_announced(value) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def announced_age_days(record: dict) -> int | None:
    d = parse_announced((record.get("dates") or {}).get("announced"))
    return (date.today() - d).days if d else None


def slugify(s: str) -> str:
    # Fold accents first so "Nestlé" becomes "nestle", not "nestl".
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s


def build_id(record: dict) -> str:
    """Stable ID derived from announced-date + company + location."""
    date_part = record["dates"]["announced"][:7]  # YYYY-MM
    company = slugify(record["company"]["name"])[:30]
    city = slugify(record["location"].get("city") or "")[:20]
    state = (record["location"].get("state") or "").lower()
    parts = [date_part, company]
    if city:
        parts.append(city)
    if state:
        parts.append(state)
    return "-".join(parts)
