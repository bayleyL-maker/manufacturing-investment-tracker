"""
Step 5: Review workflow.

Walks through each record in data/pending.json and lets you approve, reject,
edit, or skip it. Approved records move to data/investments.json. Rejected
records move to data/rejected.json (kept for audit).

Run with:
    python scripts/review.py
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PENDING_PATH = DATA_DIR / "pending.json"
APPROVED_PATH = DATA_DIR / "investments.json"
REJECTED_PATH = DATA_DIR / "rejected.json"

EDITOR = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "vi")


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


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_amount(record):
    if not record.get("amount_disclosed") or record.get("amount_usd") is None:
        return "Undisclosed"
    usd = record["amount_usd"]
    if usd >= 1_000_000_000:
        return f"${usd/1_000_000_000:.2f}B"
    if usd >= 1_000_000:
        return f"${usd/1_000_000:.0f}M"
    return f"${usd:,}"


def print_record(r, index, total):
    print("\n" + "=" * 70)
    print(f"  Record {index + 1} of {total}")
    print("=" * 70)
    print(f"  Company:    {r['company']['name']}  ({r['company'].get('hq_country', '?')})")
    print(f"  Industry:   {r['industry']}")
    print(f"  Type:       {r['investment_type']}")
    print(f"  Amount:     {format_amount(r)}")
    loc = r["location"]
    loc_text = f"{loc.get('city') or '(no city)'}, {loc.get('state')} ({loc.get('precision')})"
    print(f"  Location:   {loc_text}")
    print(f"  Announced:  {r['dates']['announced']}")
    print(f"  Expected:   start={r['dates'].get('expected_start')}  complete={r['dates'].get('expected_completion')}")
    print(f"  Sources:")
    for s in r.get("sources", []):
        print(f"    - {s.get('publication')}: {s.get('url')}")
    print(f"\n  Description:")
    print(f"    {r.get('description', '')}")
    print(f"\n  ID: {r['id']}")


def edit_record(r):
    """Open the record in $EDITOR for hand-editing, return the updated dict."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(r, f, indent=2, ensure_ascii=False)
        tmp_path = f.name
    try:
        subprocess.call([EDITOR, tmp_path])
        with open(tmp_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"  ERROR: edited file is not valid JSON: {e}")
        print("  Keeping the original record.")
        return r
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def prompt():
    while True:
        choice = input("\n  [a]pprove  [r]eject  [e]dit  [s]kip  [q]uit > ").strip().lower()
        if choice in ("a", "r", "e", "s", "q"):
            return choice
        print("  Please enter one of: a, r, e, s, q")


def main():
    pending = load_json(PENDING_PATH, [])
    approved = load_json(APPROVED_PATH, [])
    rejected = load_json(REJECTED_PATH, [])

    if not pending:
        print("Nothing pending. Run pipeline.py first.")
        return

    print(f"{len(pending)} record(s) pending review.")
    print(f"Editor: {EDITOR}  (set $EDITOR env var to override)")

    # Iterate over a copy so we can mutate `pending` safely
    remaining = list(pending)
    next_remaining = []
    i = 0
    total = len(remaining)

    while i < len(remaining):
        r = remaining[i]
        print_record(r, i, total)
        action = prompt()

        if action == "a":
            r["review"] = {"status": "approved", "reviewed_at": now_iso()}
            approved.append(r)
            print(f"  -> approved.")
        elif action == "r":
            r["review"] = {"status": "rejected", "reviewed_at": now_iso()}
            rejected.append(r)
            print(f"  -> rejected.")
        elif action == "e":
            updated = edit_record(r)
            remaining[i] = updated
            # don't advance; re-show the edited record
            continue
        elif action == "s":
            next_remaining.append(r)
            print(f"  -> skipped (stays pending).")
        elif action == "q":
            # everything from i onward stays pending
            next_remaining.extend(remaining[i:])
            break
        i += 1

    save_json(PENDING_PATH, next_remaining)
    save_json(APPROVED_PATH, approved)
    save_json(REJECTED_PATH, rejected)

    print("\n--- done ---")
    print(f"  Approved: {len(approved)} total")
    print(f"  Rejected: {len(rejected)} total")
    print(f"  Pending:  {len(next_remaining)} remaining")


if __name__ == "__main__":
    main()
