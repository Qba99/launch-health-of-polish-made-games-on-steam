"""Offline tests: a fake Steam stands in for the real API, so no network is used.

Run from the repo root:  python -m unittest discover tests -v
"""
import csv
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import fetch_reviews as fr  # noqa: E402
import steam_api  # noqa: E402
import test_call  # noqa: E402

LAUNCH = date(2024, 3, 4)
START, END = fr.launch_window(LAUNCH)


def make_reviews(n, first_id=1):
    return [{"recommendationid": str(first_id + i), "language": "english" if i % 3 else "polish",
             "timestamp_created": END - 3600 * (first_id + i), "timestamp_updated": END,
             "voted_up": i % 4 != 0, "steam_purchase": i % 5 != 0, "refunded": False,
             "written_during_early_access": False,
             "author": {"steamid": "7656119" + str(i), "playtime_at_review": 30 * i}}
            for i in range(n)]


class FakeResponse:
    def __init__(self, status=200, body=None, url="https://fake"):
        self.status_code, self._body, self.url, self.headers = status, body, url, {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeSteam:
    """Serves review pages by cursor; records every call's params."""

    def __init__(self, n_reviews=250, per_page=100, repeat_cursor_at=None, errors=()):
        self.reviews = make_reviews(n_reviews)
        self.per_page, self.repeat_cursor_at = per_page, repeat_cursor_at
        self.errors = list(errors)          # status codes to return first
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if self.errors:
            return FakeResponse(self.errors.pop(0), {"error": "fake"})
        if "storesearch" in url:
            return FakeResponse(body={"items": [{"id": 999, "name": "The Thaumaturge"}]})
        if "appdetails" in url:
            return FakeResponse(body={"999": {"success": True, "data": {
                "name": "The Thaumaturge", "release_date": {"date": "4 Mar, 2024"}}}})
        cursor = params["cursor"]
        page = 0 if cursor == "*" else int(cursor.split("+")[1].split("/")[0])
        chunk = [dict(r, author=dict(r["author"]))
                 for r in self.reviews[page * self.per_page:(page + 1) * self.per_page]]
        nxt = f"AoJ+{page + 1}/x=="
        if self.repeat_cursor_at is not None and page >= self.repeat_cursor_at:
            nxt = cursor
        body = {"response": {"reviews": chunk, "cursor": nxt,
                             "total_matching": len(self.reviews)}}
        if page == 0:
            body["response"]["query_summary"] = {"num_reviews": len(chunk)}
        return FakeResponse(body=body)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patches = [mock.patch.object(steam_api, "PAUSE_SECONDS", 0),
                   mock.patch.object(steam_api.time, "sleep", lambda s: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class TestWindowAndParams(Base):
    def test_window_covers_7_days_before_to_end_of_day_90(self):
        utc = lambda *a: int(datetime(*a, tzinfo=timezone.utc).timestamp())
        self.assertEqual(START, utc(2024, 2, 26))                 # launch - 7 days
        self.assertEqual(END, utc(2024, 6, 2, 23, 59, 59))         # end of day 90

    def test_params_override_biased_defaults(self):
        p = fr.build_params(1, START, END, "abc")
        self.assertEqual(p["languages[0]"], "all")
        self.assertEqual(p["purchase_type"], 1)
        self.assertEqual(p["filter"], 1)
        self.assertEqual(p["filter_offtopic_activity"], "false")
        self.assertEqual((p["date_range_start"], p["date_range_end"], p["cursor"]), (START, END, "abc"))

    def test_release_date_formats(self):
        self.assertEqual(steam_api.parse_release_date("4 Mar, 2024"), LAUNCH)
        self.assertEqual(steam_api.parse_release_date("Mar 4, 2024"), LAUNCH)
        self.assertIsNone(steam_api.parse_release_date("Coming soon"))


class TestFetch(Base):
    def test_full_download_is_complete_and_private(self):
        steam = FakeSteam(250)
        s = fr.fetch_reviews(999, LAUNCH, out_dir=self.tmp, session=steam)
        self.assertEqual((s["unique_reviews"], s["steam_total_matching"]), (250, 250))
        self.assertEqual((s["pages"], s["count_check"], s["steamid_left"]), (3, "PASS", 0))
        self.assertEqual(s["stop_reason"], "empty page")
        raw = (self.tmp / "999" / "page_00001.json").read_text(encoding="utf-8")
        self.assertNotIn("steamid", raw)

    def test_resume_after_interruption_has_no_duplicates(self):
        steam = FakeSteam(250)
        first = fr.fetch_reviews(999, LAUNCH, out_dir=self.tmp, session=steam, max_pages=1)
        self.assertEqual(first["count_check"], "IN PROGRESS")
        s = fr.fetch_reviews(999, LAUNCH, out_dir=self.tmp, session=steam)
        self.assertEqual((s["unique_reviews"], s["duplicates"]), (250, 0))
        cursors = [c[1]["cursor"] for c in steam.calls]
        self.assertEqual(cursors.count("*"), 1)       # page 1 never fetched twice

    def test_finished_game_is_skipped(self):
        steam = FakeSteam(50)
        fr.fetch_reviews(999, LAUNCH, out_dir=self.tmp, session=steam)
        n = len(steam.calls)
        fr.fetch_reviews(999, LAUNCH, out_dir=self.tmp, session=steam)
        self.assertEqual(len(steam.calls), n)

    def test_repeated_cursor_stops_the_loop(self):
        steam = FakeSteam(1000, repeat_cursor_at=1)
        s = fr.fetch_reviews(999, LAUNCH, out_dir=self.tmp, session=steam)
        self.assertEqual(s["stop_reason"], "cursor repeated")
        self.assertLess(len(steam.calls), 5)

    def test_different_window_is_refused(self):
        fr.fetch_reviews(999, LAUNCH, out_dir=self.tmp, session=FakeSteam(10))
        with self.assertRaises(ValueError):
            fr.fetch_reviews(999, date(2024, 3, 5), out_dir=self.tmp, session=FakeSteam(10))


class TestRetries(Base):
    def test_429_then_success(self):
        steam = FakeSteam(10, errors=[429, 503])
        data = steam_api.get_json(steam, steam_api.REVIEWS_URL, fr.build_params(1, START, END))
        self.assertEqual(len(data["response"]["reviews"]), 10)
        self.assertEqual(len(steam.calls), 3)

    def test_bad_request_fails_fast(self):
        steam = FakeSteam(10, errors=[400])
        with self.assertRaises(steam_api.SteamAPIError):
            steam_api.get_json(steam, steam_api.REVIEWS_URL, {})
        self.assertEqual(len(steam.calls), 1)

    def test_gives_up_after_max_retries(self):
        steam = FakeSteam(10, errors=[429] * 10)
        with self.assertRaises(steam_api.SteamAPIError):
            steam_api.get_json(steam, steam_api.REVIEWS_URL, {})
        self.assertEqual(len(steam.calls), steam_api.MAX_RETRIES)


class TestCsvAndTestCall(Base):
    def test_csv_skips_incomplete_rows_and_sorts_smallest_first(self):
        path = self.tmp / "games.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["game", "steam_app_id", "launch_date", "reviews_at_selection_oct2026"])
            w.writerows([["Big", "1", "2020-12-10", "885274"], ["Small", "2", "2024-03-04", "3918"],
                         ["NoId", "", "2024-01-01", "100"]])
        self.assertEqual([g["game"] for g in fr.games_from_csv(path)], ["Small", "Big"])

    def test_test_call_end_to_end(self):
        steam = FakeSteam(150)
        with mock.patch.object(steam_api, "make_session", return_value=steam), \
             mock.patch.object(test_call, "TEST_DIR", self.tmp), \
             mock.patch("builtins.print"):
            test_call.results.clear()
            code = test_call.main([])
        report = json.loads((self.tmp / "test_call_report.json").read_text(encoding="utf-8"))
        fails = [c for c in report["checks"] if c["status"] == "FAIL"]
        self.assertEqual((code, fails), (0, []))
        self.assertEqual(report["launch_date"], "2024-03-04")


if __name__ == "__main__":
    unittest.main()
