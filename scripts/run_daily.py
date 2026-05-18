"""
Daily ingest wrapper: runs pipeline -> geocode -> match_suppliers in sequence.

This is what the GitHub Actions cron executes. You can also run it locally
to test the full ingest flow.

Run with:
    python scripts/run_daily.py
"""
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
STEPS = [
    ("pipeline.py", "Fetch feed and extract investments"),
    ("geocode.py", "Geocode city locations"),
    ("match_suppliers.py", "Match likely suppliers"),
]

def main():
    for script, label in STEPS:
        print(f"\n{'='*70}")
        print(f"  {label}  ({script})")
        print(f"{'='*70}")
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / script)],
            check=False,
        )
        if result.returncode != 0:
            print(f"\nStep '{script}' exited with code {result.returncode}. Continuing.")

    print("\nDone.")

if __name__ == "__main__":
    main()
