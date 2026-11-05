"""Phase 3, patch dates: list each game's official announcements in its launch
window and suggest its first major patch, for you to confirm by hand.

For every game with a launch_date in game_list_final.csv this script
1. pages back through ISteamNews/GetNewsForApp (official developer posts only),
2. keeps posts from launch day to day 90 and saves them raw to data/raw/news/,
3. flags posts that look like patch notes or hotfixes,
4. writes data/clean/patch_candidates.csv with a `pick` column: the suggested
   first major patch is marked 'suggested'.

YOUR STEP: open patch_candidates.csv (Excel is fine), read the suggested row
for each game, and write `yes` in `pick` on the row you judge to be the first
major patch (the suggestion or another one). One `yes` per game; leave a game
without `yes` if it had no real patch in 90 days. Re-running this script keeps
your `yes` marks. Excel is fine: a semicolon-separated save (Polish Excel) is
read correctly, and picks are matched by game + date + title, not by gid.

Usage (from the repo root):
    python src/fetch_news.py --game "The Thaumaturge"    # try one game first
    python src/fetch_news.py
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import steam_api

REPO_ROOT = Path(__file__).resolve().parents[1]
GAME_LIST = REPO_ROOT / "game_list_final.csv"
RAW_NEWS_DIR = REPO_ROOT / "data" / "raw" / "news"
CANDIDATES = REPO_ROOT / "data" / "clean" / "patch_candidates.csv"
NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
OFFICIAL_FEED = "steam_community_announcements"
DAYS_AFTER_LAUNCH = 90

# Title or opening words that mark patch notes (English + Polish).
PATCH_RE = re.compile(r"\b(patch|hotfix|hot-fix|update|fix(es|ed)?|changelog|patch ?notes|"
                      r"v?\d+\.\d+(\.\d+)?|aktualizacj|poprawk|łatk)", re.IGNORECASE)
HOTFIX_RE = re.compile(r"\b(hotfix|hot-fix|quick ?fix|small fix|minor)", re.IGNORECASE)
MAJOR_MIN_LENGTH = 1500   # characters of patch notes for a post to count as "major"

COLUMNS = ["app_id", "game", "launch_date", "day_n", "post_date", "title", "url",
           "notes_length", "looks_like_patch", "looks_like_hotfix", "pick", "gid"]


def launch_ts(launch: date) -> int:
    return int(datetime(launch.year, launch.month, launch.day, tzinfo=timezone.utc).timestamp())


def fetch_window(session, app_id: int, start_ts: int, end_ts: int) -> list[dict]:
    """All official posts dated start_ts..end_ts, paging backwards with enddate."""
    items, enddate, last_oldest = {}, end_ts + 1, None
    for _ in range(50):                     # safety cap: 5,000 posts is far more than 90 days hold
        data = steam_api.get_json(session, NEWS_URL, {
            "appid": app_id, "count": 100, "maxlength": 0,
            "enddate": enddate, "feeds": OFFICIAL_FEED})
        batch = (data.get("appnews") or {}).get("newsitems") or []
        for item in batch:
            if start_ts <= item.get("date", 0) <= end_ts:
                items[item["gid"]] = item
        oldest = min((i.get("date", 0) for i in batch), default=0)
        if len(batch) < 100 or oldest < start_ts:
            break
        # Next page: Steam returns posts *earlier than* enddate, so overlap by one second
        # to keep posts sharing the boundary timestamp (duplicates are dropped by gid).
        enddate = oldest + 1 if oldest != last_oldest else oldest
        last_oldest = oldest
    return sorted(items.values(), key=lambda i: i["date"])


def classify(item: dict, day0: int) -> dict:
    title = item.get("title") or ""
    head = title + " " + (item.get("contents") or "")[:200]
    return {
        "day_n": (item["date"] - day0) // 86400,
        "post_date": datetime.fromtimestamp(item["date"], timezone.utc).date().isoformat(),
        "title": " ".join(title.split()),
        "url": item.get("url", ""),
        "notes_length": len(item.get("contents") or ""),
        "looks_like_patch": bool(PATCH_RE.search(head)),
        "looks_like_hotfix": bool(HOTFIX_RE.search(title)),
        "gid": str(item.get("gid", "")),
    }


def suggest_first_major(rows: list[dict]) -> str | None:
    """First post-launch patch post that is long and not a hotfix; else first patch post."""
    patches = [r for r in rows if r["looks_like_patch"] and r["day_n"] >= 1]
    major = [r for r in patches if not r["looks_like_hotfix"] and r["notes_length"] >= MAJOR_MIN_LENGTH]
    pick = (major or patches or [None])[0]
    return pick["gid"] if pick else None


def read_candidates(path: Path | None = None) -> list[dict]:
    """Read the candidates CSV whether it was saved with commas or (Polish Excel) semicolons."""
    path = path or CANDIDATES
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        header = fh.readline()
        fh.seek(0)
        delimiter = ";" if header.count(";") > header.count(",") else ","
        return [{k.strip(): (v or "").strip() for k, v in r.items() if k}
                for r in csv.DictReader(fh, delimiter=delimiter)]


def normalise_date(text: str) -> str:
    """'2024-03-10', '10.03.2024' (Polish Excel) or '10/03/2024' -> '2024-03-10'."""
    text = str(text).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def pick_key(row: dict) -> tuple:
    """Stable across Excel edits (unlike gid, which Excel turns into 1.23E+18)."""
    return (str(row["app_id"]).strip(), normalise_date(row["post_date"]),
            " ".join(str(row["title"]).split()))


def read_existing_picks() -> set:
    return {pick_key(r) for r in read_candidates() if r.get("pick", "").lower() == "yes"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game", help="only this game (exact name from the CSV)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    with open(GAME_LIST, encoding="utf-8-sig", newline="") as fh:
        games = [g for g in csv.DictReader(fh) if g.get("launch_date") and g.get("steam_app_id")]
    if args.game:
        games = [g for g in games if g["game"] == args.game]
        if not games:
            print(f"No game called '{args.game}' with a launch_date in {GAME_LIST.name}")
            return 1

    kept_picks = read_existing_picks()
    old_rows = []
    if args.game:                              # single-game run: keep the other games' rows
        old_rows = [r for r in read_candidates() if r.get("game") != args.game]

    RAW_NEWS_DIR.mkdir(parents=True, exist_ok=True)
    session = steam_api.make_session()
    rows = []
    for g in games:
        app_id, launch = int(g["steam_app_id"]), date.fromisoformat(g["launch_date"])
        day0 = launch_ts(launch)
        items = fetch_window(session, app_id, day0, day0 + (DAYS_AFTER_LAUNCH + 1) * 86400 - 1)
        with open(RAW_NEWS_DIR / f"{app_id}.json", "w", encoding="utf-8") as fh:
            json.dump(items, fh, ensure_ascii=False, indent=1)

        game_rows = [{"app_id": app_id, "game": g["game"], "launch_date": g["launch_date"],
                      **classify(i, day0), "pick": ""} for i in items]
        suggested = suggest_first_major(game_rows)
        for r in game_rows:
            if pick_key(r) in kept_picks:
                r["pick"] = "yes"
            elif r["gid"] == suggested:
                r["pick"] = "suggested"
        rows += game_rows

        s = next((r for r in game_rows if r["gid"] == suggested), None)
        n_patch = sum(r["looks_like_patch"] for r in game_rows)
        print(f"{g['game']:<26}{len(game_rows):>4} posts, {n_patch:>3} patch-like  "
              + (f"suggested: day {s['day_n']:>2}  {s['title'][:45]}" if s else "no patch found"))

    CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    tmp = CANDIDATES.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:   # BOM: Excel shows Polish letters right
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(old_rows + rows)
    os.replace(tmp, CANDIDATES)
    print(f"\nWrote {CANDIDATES.relative_to(REPO_ROOT)}. Next: mark one row per game with pick = yes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
