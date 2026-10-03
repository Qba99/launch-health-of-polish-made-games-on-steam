"""Download every Steam review written in a game's launch window.

For each game this script pages through IUserReviewsService/GetAppReviews and
saves every raw page as JSON under data/raw/reviews/<app_id>/. It is built to
run unattended for hours:

- 1-second pause between calls, back-off on HTTP 429/5xx (see steam_api.py)
- resumable: progress is kept in _state.json, so re-running continues where a
  crash or Ctrl+C stopped it, and finished games are skipped
- privacy: author.steamid is removed before anything is written to disk
- self-checking: when a game finishes, _summary.json compares the number of
  unique reviews saved with Steam's own count for the same window

Usage (from the repo root):
    python src/fetch_reviews.py --app-id 2147040 --launch-date 2024-03-04
    python src/fetch_reviews.py --from-csv game_list_final.csv
CSV mode needs `steam_app_id` and `launch_date` (YYYY-MM-DD) columns; rows
without them are skipped. Games run smallest first, so Cyberpunk goes last.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import steam_api

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_REVIEWS_DIR = REPO_ROOT / "data" / "raw" / "reviews"

DAYS_AFTER_LAUNCH = 90   # analysis window: day 0 .. day 90
DAYS_BEFORE_LAUNCH = 7   # also catch pre-release reviews (press/key copies); flagged later

# Request settings. The API defaults would bias the data, so each is set on purpose.
BASE_PARAMS = {
    "filter": 1,                          # 1 = Recent. Default (Helpful) does not list every review
    "languages[0]": "all",                # default = English only
    "review_type": 0,                     # 0 = positive and negative
    "purchase_type": 1,                   # 1 = all purchases; default = Steam purchases only
    "filter_offtopic_activity": "false",  # keep review-bomb periods (default hides them)
    "num_per_page": 100,                  # maximum page size
}

log = logging.getLogger("fetch_reviews")


# --- Small, testable pieces ------------------------------------------------
def launch_window(launch: date, days_after: int = DAYS_AFTER_LAUNCH,
                  days_before: int = DAYS_BEFORE_LAUNCH) -> tuple[int, int]:
    """Unix timestamps for [launch - days_before, end of launch + days_after] in UTC."""
    day0 = datetime(launch.year, launch.month, launch.day, tzinfo=timezone.utc)
    start = day0 - timedelta(days=days_before)
    end = day0 + timedelta(days=days_after + 1) - timedelta(seconds=1)
    return int(start.timestamp()), int(end.timestamp())


def build_params(app_id: int, start_ts: int, end_ts: int, cursor: str = "*") -> dict:
    params = dict(BASE_PARAMS)
    params.update({
        "appid": app_id,
        "date_range_start": start_ts,   # both must be set, or Steam ignores them
        "date_range_end": end_ts,
        "cursor": cursor,
    })
    return params


def strip_personal_data(review: dict) -> dict:
    """Remove the reviewer's Steam ID. Nothing else identifies a person."""
    author = review.get("author")
    if isinstance(author, dict):
        author.pop("steamid", None)
    return review


def _write_json(path: Path, obj) -> None:
    """Write JSON atomically, so a crash never leaves a half-written file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _read_json(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --- Core ------------------------------------------------------------------
def fetch_reviews(app_id: int, launch: date, out_dir: Path = RAW_REVIEWS_DIR,
                  days_after: int = DAYS_AFTER_LAUNCH,
                  days_before: int = DAYS_BEFORE_LAUNCH,
                  session=None, max_pages: int | None = None) -> dict:
    """Fetch all reviews in a game's launch window; return the game's summary.

    max_pages limits pages fetched in THIS run (useful for testing); the game is
    then left unfinished and a later run resumes it.
    """
    session = session or steam_api.make_session()
    start_ts, end_ts = launch_window(launch, days_after, days_before)
    game_dir = Path(out_dir) / str(app_id)
    game_dir.mkdir(parents=True, exist_ok=True)
    state_path = game_dir / "_state.json"
    window = {"launch_date": launch.isoformat(), "start_ts": start_ts, "end_ts": end_ts}

    state = None
    if state_path.exists():
        state = _read_json(state_path)
        if state.get("window") != window:
            raise ValueError(
                f"{game_dir} holds a download for a different window {state.get('window')}. "
                "Move or delete that folder before fetching a new window.")
        if state.get("done"):
            log.info("App %s already complete (%s pages) - skipping", app_id, state["pages"])
            return summarise_game(game_dir)
        log.info("App %s: resuming at page %d", app_id, state["pages"] + 1)
    if state is None:
        state = {"app_id": app_id, "window": window, "cursor": "*", "pages": 0,
                 "reviews_saved": 0, "seen_cursors": [], "total_matching": None,
                 "query_summary": None, "done": False, "stop_reason": None}

    pages_this_run = 0
    while True:
        if max_pages is not None and pages_this_run >= max_pages:
            log.info("App %s: stopped after %d pages (max_pages); re-run to resume",
                     app_id, pages_this_run)
            break

        cursor = state["cursor"]
        payload = steam_api.get_json(session, steam_api.REVIEWS_URL,
                                     build_params(app_id, start_ts, end_ts, cursor))
        response = payload.get("response", payload)
        reviews = response.get("reviews") or []

        if state["total_matching"] is None and response.get("total_matching") is not None:
            state["total_matching"] = response["total_matching"]
        if state["query_summary"] is None and response.get("query_summary"):
            state["query_summary"] = response["query_summary"]

        if not reviews:
            state.update(done=True, stop_reason="empty page")
            _write_json(state_path, state)
            break

        for review in reviews:
            strip_personal_data(review)
        page_no = state["pages"] + 1
        _write_json(game_dir / f"page_{page_no:05d}.json", {
            "meta": {"app_id": app_id, "page": page_no, "cursor_used": cursor,
                     "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     **window},
            "response": response,
        })
        pages_this_run += 1

        next_cursor = response.get("cursor")
        state["seen_cursors"].append(cursor)
        state.update(pages=page_no, reviews_saved=state["reviews_saved"] + len(reviews),
                     cursor=next_cursor or cursor)

        if not next_cursor:
            state.update(done=True, stop_reason="no cursor returned")
        elif next_cursor in state["seen_cursors"]:
            # Steam occasionally hands back a cursor it already gave; that means the end.
            state.update(done=True, stop_reason="cursor repeated")
        _write_json(state_path, state)

        if page_no % 10 == 0 or state["done"]:
            expected = state["total_matching"] or "?"
            log.info("App %s: page %d, %d / %s reviews", app_id, page_no,
                     state["reviews_saved"], expected)
        if state["done"]:
            break

    return summarise_game(game_dir)


def summarise_game(game_dir: Path) -> dict:
    """Re-read the saved pages and check them against Steam's own count."""
    game_dir = Path(game_dir)
    state = _read_json(game_dir / "_state.json")
    start_ts, end_ts = state["window"]["start_ts"], state["window"]["end_ts"]
    ids, created, outside, steamid_left = set(), [], 0, 0
    for page in sorted(game_dir.glob("page_*.json")):
        for review in _read_json(page)["response"].get("reviews", []):
            ids.add(review.get("recommendationid"))
            ts = review.get("timestamp_created")
            if ts is not None:
                created.append(ts)
                if not start_ts <= ts <= end_ts:
                    outside += 1
            if "steamid" in (review.get("author") or {}):
                steamid_left += 1

    expected = state.get("total_matching")
    unique = len(ids)
    diff_pct = round(100 * (unique - expected) / expected, 2) if expected else None
    to_date = lambda ts: datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
    summary = {
        "app_id": state["app_id"],
        "launch_date": state["window"]["launch_date"],
        "done": state["done"],
        "stop_reason": state["stop_reason"],
        "pages": state["pages"],
        "reviews_saved": state["reviews_saved"],
        "unique_reviews": unique,
        "duplicates": state["reviews_saved"] - unique,
        "steam_total_matching": expected,
        "diff_vs_steam_pct": diff_pct,
        "count_check": ("PASS" if diff_pct is not None and abs(diff_pct) <= 1
                        else "CHECK" if state["done"] else "IN PROGRESS"),
        "outside_window": outside,
        "steamid_left": steamid_left,
        "first_review_date": to_date(min(created)) if created else None,
        "last_review_date": to_date(max(created)) if created else None,
    }
    _write_json(game_dir / "_summary.json", summary)
    return summary


# --- Command line ----------------------------------------------------------
def games_from_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    games, skipped = [], []
    for row in rows:
        app_id, launch = (row.get("steam_app_id") or "").strip(), (row.get("launch_date") or "").strip()
        if not app_id or not launch:
            skipped.append(row.get("game", "?"))
            continue
        games.append({"game": row.get("game"), "app_id": int(app_id),
                      "launch": date.fromisoformat(launch),
                      "size": int(row.get("reviews_at_selection_oct2026") or 0)})
    if skipped:
        log.warning("Skipping %d games without steam_app_id/launch_date: %s",
                    len(skipped), ", ".join(skipped))
    return sorted(games, key=lambda g: g["size"])  # smallest first, Cyberpunk last


def setup_logging() -> None:
    RAW_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(RAW_REVIEWS_DIR / "fetch_log.txt", encoding="utf-8")])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--app-id", type=int, help="Steam app ID of one game")
    target.add_argument("--from-csv", type=Path, help="game list with steam_app_id and launch_date")
    parser.add_argument("--launch-date", type=date.fromisoformat, help="YYYY-MM-DD (with --app-id)")
    parser.add_argument("--days-after", type=int, default=DAYS_AFTER_LAUNCH)
    parser.add_argument("--days-before", type=int, default=DAYS_BEFORE_LAUNCH)
    parser.add_argument("--max-pages", type=int, help="stop each game after N pages (testing)")
    args = parser.parse_args(argv)
    # Windows consoles can choke on characters such as ™; replace instead of crashing
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    setup_logging()

    if args.app_id:
        if not args.launch_date:
            parser.error("--launch-date is required with --app-id")
        games = [{"game": str(args.app_id), "app_id": args.app_id, "launch": args.launch_date}]
    else:
        games = games_from_csv(args.from_csv)

    session = steam_api.make_session()
    failures = 0
    for g in games:
        log.info("=== %s (app %s, launch %s) ===", g["game"], g["app_id"], g["launch"])
        try:
            s = fetch_reviews(g["app_id"], g["launch"], days_after=args.days_after,
                              days_before=args.days_before, session=session,
                              max_pages=args.max_pages)
            log.info("%s: %s unique reviews, Steam says %s -> %s",
                     g["game"], s["unique_reviews"], s["steam_total_matching"], s["count_check"])
        except steam_api.SteamAPIError as exc:
            failures += 1
            log.error("%s failed: %s (re-run later to resume)", g["game"], exc)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
