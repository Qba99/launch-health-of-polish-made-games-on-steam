"""Phase 1, step 1: fill in Steam app IDs and launch dates, and check them.

For every game in game_list_final.csv this script
1. finds the Steam app ID (unless steam_app_id is already filled in),
2. reads the store page data: Steam's name, type, release date, developers, genres,
3. asks the reviews API three counts: lifetime reviews, reviews in the
   launch window, and reviews written BEFORE the window. A real launch date has
   (almost) nothing before it, so a big "before" count means the date is wrong,
   typically because Steam shows the 1.0 date of an Early Access game.

It writes the results back into game_list_final.csv. `launch_date` is filled
only when the date is trustworthy, and fetch_reviews.py skips rows where it is
empty, so a wrong date can never start a multi-hour download.

To fix a flagged game, type the correct date into `launch_date_override`
(YYYY-MM-DD) and run this script again; it re-checks the override.

Usage (from the repo root):
    python src/get_app_info.py
    python src/get_app_info.py --game "Manor Lords"
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import steam_api
from fetch_reviews import BASE_PARAMS, REPO_ROOT, launch_window

GAME_LIST = REPO_ROOT / "game_list_final.csv"
RAW_APP_INFO_DIR = REPO_ROOT / "data" / "raw" / "app_info"
STEAM_EPOCH = int(datetime(2003, 1, 1, tzinfo=timezone.utc).timestamp())  # before any review

NEW_COLUMNS = [
    "steam_name", "store_release_date", "launch_date_override", "launch_date",
    "launch_date_source", "steam_developers", "genres",
    "reviews_lifetime", "reviews_before_window", "reviews_in_window",
    "window_share_pct", "est_api_calls", "check_status", "check_note",
]

log = logging.getLogger("get_app_info")


# --- Name matching ---------------------------------------------------------
def normalise(name: str) -> str:
    """'Lords of the Fallen™' -> 'lords of the fallen'."""
    name = re.sub(r"[™®©]", "", name or "").casefold()
    name = re.sub(r"[:\-–—_.,!']", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def candidate_ids(hits: list[dict], wanted: str) -> list[int]:
    """Exact (normalised) name matches first; prefix matches only if none."""
    target = normalise(wanted)
    exact = [h["id"] for h in hits if normalise(h["name"]) == target]
    if exact:
        return exact
    return [h["id"] for h in hits if normalise(h["name"]).startswith(target + " ")]


def pick_app(session, game: str, year_hint: int | None):
    """Return (app_id, details, note). Disambiguates by type == game and release year."""
    hits = steam_api.search_app(session, game)
    ids = candidate_ids(hits, game)
    if not ids:
        names = "; ".join(f"{h['name']} ({h['id']})" for h in hits[:5]) or "no hits"
        return None, None, f"no name match; search returned: {names}"

    found = []
    for app_id in ids[:5]:
        details = steam_api.get_app_details(session, app_id)
        if details and details.get("type") == "game":
            parsed = steam_api.parse_release_date((details.get("release_date") or {}).get("date"))
            found.append((app_id, details, parsed))
    if not found:
        return None, None, "matches found, but none is a base game (DLC/soundtrack only)"
    if len(found) == 1:
        return found[0][0], found[0][1], ""

    if year_hint:
        same_year = [f for f in found if f[2] and abs(f[2].year - year_hint) <= 1]
        if len(same_year) == 1:
            others = ", ".join(str(f[0]) for f in found if f is not same_year[0])
            return same_year[0][0], same_year[0][1], f"picked by release year over app(s) {others}"
    listing = "; ".join(f"{f[1].get('name')} ({f[0]}, {f[2]})" for f in found)
    return None, None, f"ambiguous, fill steam_app_id by hand: {listing}"


# --- Review counts ---------------------------------------------------------
def count_reviews(session, app_id: int, start_ts: int, end_ts: int) -> int:
    """Number of reviews created in [start_ts, end_ts], using the project's settings."""
    params = dict(BASE_PARAMS)
    params.update({"appid": app_id, "date_range_start": start_ts,
                   "date_range_end": end_ts, "cursor": "*", "num_per_page": 1})
    response = steam_api.get_json(session, steam_api.REVIEWS_URL, params)
    response = response.get("response", response)
    if response.get("total_matching") is not None:
        return int(response["total_matching"])
    return int((response.get("query_summary") or {}).get("total_reviews", 0))


def date_check(lifetime: int, before: int) -> bool:
    """A trustworthy launch date has almost no reviews before its window."""
    return before <= max(10, 0.01 * lifetime)


# --- Per game --------------------------------------------------------------
def enrich(session, row: dict, now_ts: int) -> dict:
    game = row["game"]
    year_hint = int(row["launch_year_approx"]) if row.get("launch_year_approx") else None
    notes = []

    # 1-2. App ID and store data
    app_id = int(row["steam_app_id"]) if (row.get("steam_app_id") or "").strip() else None
    if app_id:
        details = steam_api.get_app_details(session, app_id)
        if not details:
            return {**row, "check_status": "NOT FOUND",
                    "check_note": f"store has no data for app {app_id}"}
    else:
        app_id, details, note = pick_app(session, game, year_hint)
        if note:
            notes.append(note)
        if not app_id:
            return {**row, "check_status": "NOT FOUND", "check_note": "; ".join(notes)}

    RAW_APP_INFO_DIR.mkdir(parents=True, exist_ok=True)
    with open(RAW_APP_INFO_DIR / f"{app_id}.json", "w", encoding="utf-8") as fh:
        json.dump(details, fh, ensure_ascii=False, indent=1)

    store_text = (details.get("release_date") or {}).get("date") or ""
    store_date = steam_api.parse_release_date(store_text)
    developers = ", ".join(details.get("developers") or [])
    genres = "; ".join(g.get("description", "") for g in details.get("genres") or [])
    if normalise(row.get("developer", "")) not in normalise(developers):
        notes.append(f"developer on Steam is '{developers}'")

    # 3. Which date to test: a manual override wins over the store date
    override = (row.get("launch_date_override") or "").strip()
    if override:
        candidate, source = date.fromisoformat(override), "manual override"
    else:
        candidate, source = store_date, "steam store"

    out = {**row, "steam_app_id": app_id, "steam_name": details.get("name", ""),
           "store_release_date": store_date.isoformat() if store_date else store_text,
           "steam_developers": developers, "genres": genres,
           "launch_date": "", "launch_date_source": source}

    if candidate is None:
        notes.append(f"could not read the store date '{store_text}'")
        return {**out, "check_status": "CHECK DATE", "check_note": "; ".join(notes)}

    start_ts, end_ts = launch_window(candidate)
    lifetime = count_reviews(session, app_id, STEAM_EPOCH, now_ts)
    before = count_reviews(session, app_id, STEAM_EPOCH, start_ts - 1)
    in_window = count_reviews(session, app_id, start_ts, end_ts)
    out.update({
        "reviews_lifetime": lifetime, "reviews_before_window": before,
        "reviews_in_window": in_window,
        "window_share_pct": round(100 * in_window / lifetime, 1) if lifetime else "",
        "est_api_calls": math.ceil(in_window / 100) + 1,
    })

    if not date_check(lifetime, before):
        notes.append(f"{before:,} reviews before {candidate} -> the real launch is earlier"
                     + (" (Early Access start?)" if source == "steam store" else ""))
        status = "CHECK DATE"
    elif row.get("launch_type") == "early_access" and not override:
        notes.append(f"Early Access game: confirm {candidate} is the EA start, "
                     "then copy it into launch_date_override")
        status = "NEEDS EA DATE"
    else:
        status = "OK"
        out["launch_date"] = candidate.isoformat()
    return {**out, "check_status": status, "check_note": "; ".join(notes)}


# --- CSV in/out ------------------------------------------------------------
def read_rows(path: Path) -> tuple[list[dict], list[str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows, fields = list(reader), list(reader.fieldnames or [])
    fields += [c for c in NEW_COLUMNS if c not in fields]
    return rows, fields


def write_rows(path: Path, rows: list[dict], fields: list[str]) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, default=GAME_LIST)
    parser.add_argument("--game", help="only process this game (exact name from the CSV)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    rows, fields = read_rows(args.csv)
    session = steam_api.make_session()
    now_ts = int(datetime.now(timezone.utc).timestamp())
    for i, row in enumerate(rows):
        if args.game and row["game"] != args.game:
            continue
        log.info("%s ...", row["game"])
        try:
            rows[i] = enrich(session, row, now_ts)
        except steam_api.SteamAPIError as exc:
            rows[i] = {**row, "check_status": "ERROR", "check_note": str(exc)[:200]}
        write_rows(args.csv, rows, fields)          # save after every game

    print(f"\n{'game':<26}{'app id':>9}  {'launch':<11}{'in window':>10}{'share':>7}  status")
    for r in rows:
        share = f"{r.get('window_share_pct')}%" if r.get("window_share_pct") not in ("", None) else ""
        print(f"{r['game'][:25]:<26}{str(r.get('steam_app_id') or ''):>9}  "
              f"{r.get('launch_date') or '-':<11}{str(r.get('reviews_in_window') or ''):>10}"
              f"{share:>7}  {r.get('check_status', '')}")
    flagged = [r for r in rows if r.get("check_status") not in ("OK", None, "")]
    for r in flagged:
        print(f"  ! {r['game']}: {r['check_status']} - {r.get('check_note')}")
    ready = sum(r.get("check_status") == "OK" for r in rows)
    calls = sum(int(r["est_api_calls"]) for r in rows
                if r.get("check_status") == "OK" and r.get("est_api_calls"))
    print(f"\n{ready}/{len(rows)} games ready to fetch; about {calls:,} API calls "
          f"(at least {calls / 3600:.1f} h at 1 call per second; Cyberpunk is most of it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
