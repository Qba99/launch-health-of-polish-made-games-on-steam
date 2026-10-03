"""Phase 0 test call: check the Steam reviews API before building on it.

Run from the repo root:
    python src/test_call.py
    python src/test_call.py --game "The Thaumaturge" --launch-date 2024-03-04

It makes about 5 calls, saves what it gets to data/raw/test/, and prints a
PASS / WARN / FAIL table for every assumption in the project plan:
date filter, all languages, all purchase types, paging, playtime, refunds.
Send data/raw/test/test_call_report.json back for review.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import steam_api
from fetch_reviews import REPO_ROOT, build_params, launch_window, strip_personal_data

TEST_DIR = REPO_ROOT / "data" / "raw" / "test"
results: list[tuple[str, str, str]] = []


def check(name: str, status: str, detail: str) -> None:
    results.append((status, name, detail))
    print(f"  [{status:<4}] {name}: {detail}")


def save(name: str, obj) -> Path:
    path = TEST_DIR / name
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    return path


def ts_to_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game", default="The Thaumaturge")
    parser.add_argument("--app-id", type=int, help="skip the store search")
    parser.add_argument("--launch-date", type=date.fromisoformat, help="YYYY-MM-DD; skip the store lookup")
    args = parser.parse_args(argv)
    # Windows consoles can choke on characters such as ™; replace instead of crashing
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    session = steam_api.make_session()

    # 1. App ID ------------------------------------------------------------
    print("\n1. Finding the game on the Steam store")
    app_id = args.app_id
    if app_id is None:
        app_id, hits = steam_api.find_app_id(session, args.game)
        print("   search hits:", ", ".join(f"{h['name']} ({h['id']})" for h in hits[:5]) or "none")
        if app_id is None:
            check("store search", "FAIL", f"no exact match for '{args.game}'; pass --app-id")
            return finish(None)
    check("store search", "PASS", f"{args.game} = app {app_id}")

    # 2. Release date ------------------------------------------------------
    print("\n2. Reading the release date")
    launch = args.launch_date
    details = steam_api.get_app_details(session, app_id)
    if details:
        raw_date = (details.get("release_date") or {}).get("date")
        parsed = steam_api.parse_release_date(raw_date)
        print(f"   store says: '{raw_date}' -> {parsed}")
        launch = launch or parsed
    if launch is None:
        check("release date", "FAIL", "could not read it; pass --launch-date YYYY-MM-DD")
        return finish(None)
    check("release date", "PASS", f"launch day = {launch}")

    # 3. First page with the project's settings ------------------------------
    print("\n3. Page 1 with the project's settings (all languages, all purchases, launch window)")
    start_ts, end_ts = launch_window(launch)
    print(f"   window: {ts_to_date(start_ts)} -> {ts_to_date(end_ts)}")
    try:
        p1 = steam_api.get_json(session, steam_api.REVIEWS_URL, build_params(app_id, start_ts, end_ts))
    except steam_api.SteamAPIError as exc:
        check("GetAppReviews call", "FAIL", str(exc))
        legacy_probe(session, app_id)
        return finish(None)
    r1 = p1.get("response", p1)
    reviews1 = r1.get("reviews") or []
    for rv in reviews1:
        strip_personal_data(rv)
    save("page1.json", r1)
    print("   top-level keys:", sorted(r1.keys()))
    check("GetAppReviews call", "PASS" if reviews1 else "FAIL",
          f"{len(reviews1)} reviews on page 1")
    if not reviews1:
        return finish(r1)

    total = r1.get("total_matching")
    check("total_matching", "PASS" if total is not None else "WARN",
          f"Steam counts {total} reviews in the window" if total is not None
          else "missing; the completeness check will rely on query_summary.total_reviews")
    print("   query_summary:", r1.get("query_summary"))

    created = [rv.get("timestamp_created", 0) for rv in reviews1]
    outside = sum(not start_ts <= ts <= end_ts for ts in created)
    check("date filter", "PASS" if outside == 0 else "FAIL",
          f"newest {ts_to_date(max(created))}, oldest {ts_to_date(min(created))}, "
          f"{outside} outside the window"
          + ("" if outside == 0 else " -> date filter ignored; tell Claude, fallback = page full history"))
    ordered = all(a >= b for a, b in zip(created, created[1:]))
    check("newest-first order", "PASS" if ordered else "WARN",
          "sorted by creation time" if ordered else "not strictly sorted (fine if the filter works)")

    langs = Counter(rv.get("language") for rv in reviews1)
    check("all languages", "PASS" if len(langs) > 1 else "WARN",
          f"{dict(langs.most_common(6))}" + ("" if len(langs) > 1 else
          " -> only one language; the languages[0]=all format may be wrong"))

    purchases = Counter(bool(rv.get("steam_purchase")) for rv in reviews1)
    check("all purchase types", "PASS" if purchases[False] else "WARN",
          f"Steam purchases {purchases[True]}, other (keys, gifts) {purchases[False]}"
          + ("" if purchases[False] else " -> none on page 1; may be genuine, re-check on more pages"))

    playtime = [((rv.get("author") or {}).get("playtime_at_review")) for rv in reviews1]
    missing = sum(p is None for p in playtime)
    check("playtime at review", "PASS" if missing < len(reviews1) * 0.1 else "WARN",
          f"present on {len(reviews1) - missing}/{len(reviews1)} reviews (minutes)")

    for field in ("refunded", "written_during_early_access", "voted_up", "timestamp_updated"):
        present = sum(field in rv for rv in reviews1)
        values = Counter(rv.get(field) for rv in reviews1 if field in rv)
        detail = f"present on {present}/{len(reviews1)}"
        if field in ("refunded", "written_during_early_access", "voted_up"):
            detail += f", values {dict(values)}"
        check(f"field '{field}'", "PASS" if present else "WARN", detail)

    left = sum("steamid" in (rv.get("author") or {}) for rv in reviews1)
    check("steamid removed", "PASS" if left == 0 else "FAIL", f"{left} left in saved data")

    # 4. Second page ----------------------------------------------------------
    print("\n4. Page 2 (does the cursor move on?)")
    cursor = r1.get("cursor")
    if not cursor:
        check("paging", "WARN", "no cursor on page 1 (only one page in the window?)")
    else:
        p2 = steam_api.get_json(session, steam_api.REVIEWS_URL,
                                build_params(app_id, start_ts, end_ts, cursor))
        r2 = p2.get("response", p2)
        reviews2 = r2.get("reviews") or []
        for rv in reviews2:
            strip_personal_data(rv)
        save("page2.json", r2)
        ids1 = {rv["recommendationid"] for rv in reviews1}
        overlap = sum(rv["recommendationid"] in ids1 for rv in reviews2)
        if not reviews2:
            check("paging", "PASS" if total and total <= len(reviews1) else "WARN",
                  "page 2 empty" + ("" if total and total <= len(reviews1) else " though Steam counts more"))
        else:
            check("paging", "PASS" if overlap == 0 else "FAIL",
                  f"{len(reviews2)} new reviews, {overlap} repeated from page 1")

    # 5. Field list for the data dictionary ----------------------------------
    sample = reviews1[0]
    fields = {k: type(v).__name__ for k, v in sample.items() if k != "author"}
    fields.update({f"author.{k}": type(v).__name__ for k, v in (sample.get("author") or {}).items()})
    print("\n5. Fields on one review (for the data dictionary):")
    for k, v in sorted(fields.items()):
        print(f"   {k:<38} {v}")
    return finish(r1, fields, app_id, launch)


def legacy_probe(session, app_id: int) -> None:
    """If the new endpoint fails, see whether the deprecated one still answers."""
    print("\n   Trying the deprecated endpoint for comparison...")
    try:
        data = steam_api.get_json(session, steam_api.LEGACY_REVIEWS_URL.format(app_id=app_id),
                                  {"json": 1, "filter": "recent", "language": "all",
                                   "purchase_type": "all", "num_per_page": 100})
        n = len(data.get("reviews") or [])
        check("legacy endpoint", "WARN", f"still answers ({n} reviews); fallback possible")
    except steam_api.SteamAPIError as exc:
        check("legacy endpoint", "FAIL", str(exc))


def finish(response, fields=None, app_id=None, launch=None) -> int:
    counts = Counter(status for status, _, _ in results)
    print(f"\nResult: {counts['PASS']} pass, {counts['WARN']} warn, {counts['FAIL']} fail")
    report = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app_id": app_id, "launch_date": launch.isoformat() if launch else None,
        "checks": [{"status": s, "check": n, "detail": d} for s, n, d in results],
        "query_summary": (response or {}).get("query_summary"),
        "total_matching": (response or {}).get("total_matching"),
        "review_fields": fields,
    }
    path = save("test_call_report.json", report)
    print(f"Report saved to {path}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
