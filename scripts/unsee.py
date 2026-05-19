"""
Helper: remove URLs from data/seen_urls.json so the next pipeline run will
re-process them. Useful after broadening industries, prompt changes, or any
time you want articles re-evaluated.

Usage:
    python scripts/unsee.py --list                  # show all seen URLs
    python scripts/unsee.py <url> [<url> ...]       # un-see specific URLs
    python scripts/unsee.py --all                   # clear everything (asks y/n)
    python scripts/unsee.py --contains <substring>  # un-see URLs containing substring
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEEN_PATH = ROOT / "data" / "seen_urls.json"


def load():
    if not SEEN_PATH.exists():
        return []
    with SEEN_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save(urls):
    with SEEN_PATH.open("w", encoding="utf-8") as f:
        json.dump(sorted(set(urls)), f, indent=2, ensure_ascii=False)
        f.write("\n")


def cmd_list(urls):
    if not urls:
        print("(seen_urls.json is empty)")
        return
    for i, u in enumerate(sorted(urls), 1):
        print(f"{i:>4}. {u}")
    print(f"\nTotal: {len(urls)} URLs")


def cmd_remove(urls, targets):
    target_set = set(targets)
    before = len(urls)
    remaining = [u for u in urls if u not in target_set]
    removed = before - len(remaining)
    not_found = target_set - set(urls)
    save(remaining)
    print(f"Removed {removed} URL(s).")
    if not_found:
        print(f"\nNot in seen list ({len(not_found)}):")
        for u in not_found:
            print(f"  {u}")
    print(f"\nRemaining: {len(remaining)} URLs")


def cmd_contains(urls, substring):
    matches = [u for u in urls if substring in u]
    if not matches:
        print(f"No URLs contain '{substring}'.")
        return
    print(f"Found {len(matches)} URL(s) containing '{substring}':")
    for u in matches:
        print(f"  {u}")
    confirm = input(f"\nUn-see all {len(matches)}? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return
    remaining = [u for u in urls if u not in set(matches)]
    save(remaining)
    print(f"Removed {len(matches)}. Remaining: {len(remaining)}")


def cmd_all(urls):
    if not urls:
        print("Already empty.")
        return
    confirm = input(f"Clear ALL {len(urls)} seen URLs? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return
    save([])
    print("Cleared. Next pipeline run will re-process every article in the feeds.")


def main():
    args = sys.argv[1:]
    urls = load()

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--list":
        cmd_list(urls)
        return
    if args[0] == "--all":
        cmd_all(urls)
        return
    if args[0] == "--contains":
        if len(args) < 2:
            print("Need a substring. Example: --contains areadevelopment.com")
            sys.exit(1)
        cmd_contains(urls, args[1])
        return

    # Otherwise: treat all args as URLs to remove
    cmd_remove(urls, args)


if __name__ == "__main__":
    main()
