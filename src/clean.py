"""Phase 2, step 1: turn raw API pages into tidy CSV tables + a data-quality log.

Reads   data/raw/reviews/<app_id>/page_*.json   and   game_list_final.csv
Writes  data/clean/games.csv                one row per game
        data/clean/reviews.csv              one row per review (no text)
        data/clean/review_text_negative.csv text of negative reviews only
        docs/data_quality.md                every check, with its result

Games are processed one at a time and review text is streamed straight to disk,
so memory stays low even for Cyberpunk's ~369k reviews.

Usage (from the repo root):
    python src/clean.py
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw" / "reviews"
CLEAN_DIR = REPO_ROOT / "data" / "clean"
GAME_LIST = REPO_ROOT / "game_list_final.csv"
DQ_REPORT = REPO_ROOT / "docs" / "data_quality.md"

INT32_MAX = 2**31 - 1   # Steam sometimes returns 4294967295 (a -1 overflow) in vote counts
PLAYTIME_BUCKETS = [(120, "0-2h", 1), (300, "2-5h", 2), (1200, "5-20h", 3), (None, "20h+", 4)]

REVIEW_COLUMNS = [
    "recommendation_id", "app_id", "language", "created_at", "updated_at", "created_date",
    "day_n", "is_prerelease", "voted_up", "is_edited", "votes_up", "votes_funny",
    "weighted_vote_score", "comment_count", "steam_purchase", "received_for_free",
    "written_during_early_access", "primarily_steam_deck", "refunded", "has_dev_response",
    "author_num_reviews", "playtime_at_review_min", "playtime_forever_min",
    "playtime_bucket", "playtime_bucket_order", "review_length",
]
GAME_COLUMNS = [
    "app_id", "game", "developer", "steam_name", "cohort", "launch_type", "launch_date",
    "launch_date_source", "genres", "role", "reviews_lifetime", "reviews_in_window",
    "reviews_collected",
]


def playtime_bucket(minutes):
    if minutes is None:
        return None, None
    for limit, label, order in PLAYTIME_BUCKETS:
        if limit is None or minutes < limit:
            return label, order
    return None, None


def clean_text(text: str | None) -> str:
    """PostgreSQL cannot store the NUL character, which a few reviews contain."""
    return (text or "").replace("\x00", "")


def safe_count(value):
    """Vote counts above the 32-bit range are a known Steam glitch -> unknown."""
    if value is None or value < 0 or value > INT32_MAX:
        return None
    return value


def flatten(review: dict, app_id: int, launch_ts: int) -> dict:
    author = review.get("author") or {}
    created, updated = review["timestamp_created"], review["timestamp_updated"]
    day_n = (created - launch_ts) // 86400
    playtime = author.get("playtime_at_review")
    bucket, order = playtime_bucket(playtime)
    return {
        "recommendation_id": int(review["recommendationid"]),
        "app_id": app_id,
        "language": review.get("language") or "unknown",
        "created_at": datetime.fromtimestamp(created, timezone.utc).isoformat(),
        "updated_at": datetime.fromtimestamp(updated, timezone.utc).isoformat(),
        "created_date": datetime.fromtimestamp(created, timezone.utc).date().isoformat(),
        "day_n": day_n,
        "is_prerelease": day_n < 0,
        "voted_up": bool(review.get("voted_up")),
        "is_edited": updated > created,
        "votes_up": safe_count(review.get("votes_up")),
        "votes_funny": safe_count(review.get("votes_funny")),
        "weighted_vote_score": review.get("weighted_vote_score"),
        "comment_count": safe_count(review.get("comment_count")),
        "steam_purchase": review.get("steam_purchase"),
        "received_for_free": review.get("received_for_free"),
        "written_during_early_access": review.get("written_during_early_access"),
        "primarily_steam_deck": review.get("primarily_steam_deck"),
        "refunded": review.get("refunded"),
        "has_dev_response": bool(review.get("developer_response")),
        "author_num_reviews": safe_count(author.get("num_reviews")),
        "playtime_at_review_min": playtime,
        "playtime_forever_min": author.get("playtime_forever"),
        "playtime_bucket": bucket,
        "playtime_bucket_order": order,
        "review_length": len(review.get("review") or ""),
    }


def process_game(game: dict, text_writer: csv.writer) -> tuple[pd.DataFrame, dict]:
    """Read every page of one game; return its review table and quality stats."""
    app_id = int(game["steam_app_id"])
    launch = date.fromisoformat(game["launch_date"])
    launch_ts = int(datetime(launch.year, launch.month, launch.day, tzinfo=timezone.utc).timestamp())
    game_dir = RAW_DIR / str(app_id)
    state = json.loads((game_dir / "_state.json").read_text(encoding="utf-8"))
    start_ts, end_ts = state["window"]["start_ts"], state["window"]["end_ts"]
    if state["window"]["launch_date"] != game["launch_date"]:
        raise ValueError(f"{game['game']}: raw data was fetched for launch "
                         f"{state['window']['launch_date']}, CSV says {game['launch_date']}")

    rows, seen = [], set()
    stats = {"raw": 0, "duplicates": 0, "outside_window": 0, "nul_removed": 0,
             "vote_overflow": 0, "missing_playtime": 0}
    for page in sorted(game_dir.glob("page_*.json")):
        payload = json.loads(page.read_text(encoding="utf-8"))
        for review in payload["response"].get("reviews", []):
            stats["raw"] += 1
            rid = review.get("recommendationid")
            if rid in seen:
                stats["duplicates"] += 1
                continue
            seen.add(rid)
            if not start_ts <= review["timestamp_created"] <= end_ts:
                stats["outside_window"] += 1
                continue
            text = review.get("review") or ""
            if "\x00" in text:
                stats["nul_removed"] += 1
            for key in ("votes_up", "votes_funny", "comment_count"):
                if (review.get(key) or 0) > INT32_MAX:
                    stats["vote_overflow"] += 1
            if (review.get("author") or {}).get("playtime_at_review") is None:
                stats["missing_playtime"] += 1
            row = flatten(review, app_id, launch_ts)
            rows.append(row)
            if not row["voted_up"]:
                text_writer.writerow([row["recommendation_id"], app_id, row["language"],
                                      clean_text(text)])

    df = pd.DataFrame(rows, columns=REVIEW_COLUMNS)
    for col in ("votes_up", "votes_funny", "comment_count", "author_num_reviews",
                "playtime_at_review_min", "playtime_forever_min", "playtime_bucket_order"):
        df[col] = df[col].astype("Int64")             # nullable ints: no '12.0' in the CSV
    stats["expected"] = state.get("total_matching")
    return df, stats


def game_profile(game: dict, df: pd.DataFrame, stats: dict) -> dict:
    """One line of the per-game data-quality table."""
    n = len(df)
    pct = lambda s: round(100 * s.sum() / n, 1) if n else 0.0
    ea_share = pct(df["written_during_early_access"].fillna(False).astype(bool))
    expected_ea = game["launch_type"] == "early_access"
    return {
        "game": game["game"], "reviews": n, "steam_count": stats["expected"],
        "pct_positive": pct(df["voted_up"]),
        "prerelease": int(df["is_prerelease"].sum()),
        "pct_edited": pct(df["is_edited"]),
        "pct_zero_playtime": pct(df["playtime_at_review_min"].fillna(-1) == 0),
        "pct_not_steam_purchase": pct(~df["steam_purchase"].fillna(True).astype(bool)),
        "pct_free": pct(df["received_for_free"].fillna(False).astype(bool)),
        "pct_written_in_ea": ea_share,
        "ea_flag_ok": (ea_share >= 90) if expected_ea else (ea_share <= 1),
        "refunded_true": int(df["refunded"].fillna(False).astype(bool).sum()),
        "dev_responses": int(df["has_dev_response"].sum()),
        **{k: stats[k] for k in ("duplicates", "outside_window", "missing_playtime",
                                 "vote_overflow", "nul_removed")},
    }


def write_report(profiles: list[dict], totals: dict) -> None:
    checks = [
        ("Every game has raw data", totals["games_missing"] == 0, f"{totals['games_missing']} missing"),
        ("Collected = Steam's count (within 1%)", totals["count_ok"],
         f"{totals['reviews']:,} of {totals['steam_count']:,} "
         f"({100 * totals['reviews'] / totals['steam_count']:.2f}%)"),
        ("No duplicate reviews", totals["duplicates"] == 0, f"{totals['duplicates']} dropped"),
        ("No reviews outside the window", totals["outside_window"] == 0,
         f"{totals['outside_window']} dropped"),
        ("Playtime at review present (99.9%+)", totals["missing_playtime"] <= 0.001 * totals["reviews"],
         f"{totals['missing_playtime']} missing "
         f"({100 * totals['missing_playtime'] / totals['reviews']:.3f}%); excluded from playtime analysis only"),
        ("Early Access flag matches launch type", all(p["ea_flag_ok"] for p in profiles),
         ", ".join(p["game"] for p in profiles if not p["ea_flag_ok"]) or "all games consistent"),
    ]
    info = [
        ("Pre-release reviews (day -7 to -1)", f"{totals['prerelease']:,} kept, flagged `is_prerelease`"),
        ("Edited reviews", f"{totals['edited']:,} ({100 * totals['edited'] / totals['reviews']:.1f}%); "
                           "dated by when they were written, verdict is today's"),
        ("Reviews marked `refunded`", f"{totals['refunded']:,}"),
        ("Vote counts out of range (Steam glitch)", f"{totals['vote_overflow']:,} set to empty"),
        ("Reviews with NUL characters", f"{totals['nul_removed']:,} cleaned (PostgreSQL rejects them)"),
        ("Negative reviews with text kept", f"{totals['negative']:,}"),
    ]
    lines = [
        "# Data quality log",
        "",
        f"Generated by `src/clean.py` on {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC "
        f"from {totals['games']} games and {totals['reviews']:,} reviews.",
        "",
        "## Checks",
        "",
        "| Check | Result | Detail |",
        "| --- | --- | --- |",
        *[f"| {name} | {'PASS' if ok else 'CHECK'} | {detail} |" for name, ok, detail in checks],
        "",
        "## Facts to know before analysing",
        "",
        "| Item | Value |",
        "| --- | --- |",
        *[f"| {name} | {value} |" for name, value in info],
        "",
        "## Per game",
        "",
        "Percentages are shares of each game's reviews in its window (day -7 to day 90).",
        "",
        "| Game | Reviews | % positive | Pre-release | % edited | % 0 min played | % not Steam purchase "
        "| % free copy | % written in EA | Refunded | Dev replies |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        *[f"| {p['game']} | {p['reviews']:,} | {p['pct_positive']} | {p['prerelease']:,} | "
          f"{p['pct_edited']} | {p['pct_zero_playtime']} | {p['pct_not_steam_purchase']} | "
          f"{p['pct_free']} | {p['pct_written_in_ea']} | {p['refunded_true']:,} | {p['dev_responses']:,} |"
          for p in sorted(profiles, key=lambda p: -p["reviews"])],
        "",
    ]
    DQ_REPORT.parent.mkdir(parents=True, exist_ok=True)
    DQ_REPORT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    with open(GAME_LIST, encoding="utf-8-sig", newline="") as fh:
        games = [g for g in csv.DictReader(fh)]
    games_missing = [g["game"] for g in games
                     if not g.get("launch_date") or not (RAW_DIR / str(g.get("steam_app_id")) / "_state.json").exists()]
    games = [g for g in games if g["game"] not in games_missing]

    reviews_path = CLEAN_DIR / "reviews.csv"
    text_path = CLEAN_DIR / "review_text_negative.csv"
    profiles, game_rows = [], []
    totals = dict.fromkeys(["reviews", "steam_count", "duplicates", "outside_window",
                            "missing_playtime", "prerelease", "edited", "refunded",
                            "vote_overflow", "nul_removed", "negative"], 0)

    with open(text_path, "w", encoding="utf-8", newline="") as text_fh, \
         open(reviews_path, "w", encoding="utf-8", newline="") as rev_fh:
        text_writer = csv.writer(text_fh, lineterminator="\n")
        text_writer.writerow(["recommendation_id", "app_id", "language", "review"])
        for i, game in enumerate(sorted(games, key=lambda g: int(g["steam_app_id"]))):
            df, stats = process_game(game, text_writer)
            df.to_csv(rev_fh, index=False, header=(i == 0), lineterminator="\n")
            profile = game_profile(game, df, stats)
            profiles.append(profile)
            game_rows.append({**{c: game.get(c, "") for c in GAME_COLUMNS},
                              "app_id": int(game["steam_app_id"]), "reviews_collected": len(df)})
            for key in ("duplicates", "outside_window", "missing_playtime", "vote_overflow", "nul_removed"):
                totals[key] += stats[key]
            totals["reviews"] += len(df)
            totals["steam_count"] += stats["expected"] or 0
            totals["prerelease"] += profile["prerelease"]
            totals["edited"] += int(df["is_edited"].sum())
            totals["refunded"] += profile["refunded_true"]
            totals["negative"] += int((~df["voted_up"]).sum())
            print(f"{game['game']:<26}{len(df):>9,} reviews  {profile['pct_positive']:>5}% positive")
            del df

    pd.DataFrame(game_rows, columns=GAME_COLUMNS).to_csv(
        CLEAN_DIR / "games.csv", index=False, lineterminator="\n")
    totals.update(games=len(games), games_missing=len(games_missing),
                  count_ok=abs(totals["reviews"] - totals["steam_count"]) <= 0.01 * totals["steam_count"])
    write_report(profiles, totals)
    print(f"\n{totals['reviews']:,} reviews from {len(games)} games -> {CLEAN_DIR.relative_to(REPO_ROOT)}")
    print(f"Data-quality log -> {DQ_REPORT.relative_to(REPO_ROOT)}")
    if games_missing:
        print("Missing raw data or launch_date:", ", ".join(games_missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
