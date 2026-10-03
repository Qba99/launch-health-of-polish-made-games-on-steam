"""Offline tests for get_app_info.py against a fake Steam store + reviews API.

Run from the repo root:  python -m unittest discover tests -v
"""
import csv
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import get_app_info as gai  # noqa: E402
import steam_api  # noqa: E402


def ts(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


class FakeResponse:
    def __init__(self, body):
        self.status_code, self._body, self.url, self.headers, self.text = 200, body, "fake", {}, ""

    def json(self):
        return self._body


# app_id -> (name, type, store date text, developers, review timestamps)
APPS = {
    265300: ("Lords Of The Fallen", "game", "28 Oct, 2014", ["Deck13", "CI Games"],
             [ts(2014, 11, 1)] * 500),
    1501750: ("Lords of the Fallen", "game", "13 Oct, 2023", ["HEXWORKS", "CI Games"],
              [ts(2023, 10, 20)] * 300 + [ts(2024, 6, 1)] * 100),
    534380: ("Dying Light 2 Stay Human: Reloaded Edition", "game", "4 Feb, 2022", ["Techland"],
             [ts(2022, 2, 10)] * 900),
    534381: ("Dying Light 2 Stay Human - Soundtrack", "dlc", "4 Feb, 2022", ["Techland"], []),
    # Against the Storm: store shows the 1.0 date, but reviews started in Early Access (2021)
    1336490: ("Against the Storm", "game", "8 Dec, 2023", ["Eremite Games"],
              [ts(2021, 11, 10)] * 400 + [ts(2023, 12, 10)] * 200),
    # Manor Lords: still EA, store date = EA start
    1363080: ("Manor Lords", "game", "26 Apr, 2024", ["Slavic Magic"], [ts(2024, 4, 27)] * 700),
}
SEARCH = {
    "lords of the fallen": [265300, 1501750],
    "dying light 2": [534380, 534381],
    "against the storm": [1336490],
    "manor lords": [1363080],
}


class FakeSteam:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if "storesearch" in url:
            ids = SEARCH.get(params["term"].casefold(), [])
            return FakeResponse({"items": [{"id": i, "name": APPS[i][0]} for i in ids]})
        if "appdetails" in url:
            i = int(params["appids"])
            name, typ, when, devs, _ = APPS[i]
            return FakeResponse({str(i): {"success": True, "data": {
                "name": name, "type": typ, "release_date": {"date": when},
                "developers": devs, "genres": [{"description": "Action"}]}}})
        stamps = APPS[int(params["appid"])][4]
        n = sum(params["date_range_start"] <= t <= params["date_range_end"] for t in stamps)
        return FakeResponse({"response": {"reviews": [], "total_matching": n}})


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


ROW_FIELDS = ["game", "developer", "steam_app_id", "launch_year_approx", "launch_type"]


class TestGetAppInfo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for p in [mock.patch.object(steam_api, "PAUSE_SECONDS", 0),
                  mock.patch.object(gai, "RAW_APP_INFO_DIR", self.tmp / "app_info")]:
            p.start()
            self.addCleanup(p.stop)
        self.steam = FakeSteam()
        self.now = ts(2026, 10, 3)

    def run_row(self, **row):
        base = dict.fromkeys(ROW_FIELDS, "")
        base.update(row)
        return gai.enrich(self.steam, base, self.now)

    def test_same_name_resolved_by_release_year(self):
        r = self.run_row(game="Lords of the Fallen", developer="CI Games", launch_year_approx="2023",
                         launch_type="full")
        self.assertEqual((r["steam_app_id"], r["check_status"]), (1501750, "OK"))
        self.assertEqual(r["launch_date"], "2023-10-13")
        self.assertEqual((r["reviews_in_window"], r["reviews_lifetime"]), (300, 400))
        self.assertEqual(r["window_share_pct"], 75.0)
        self.assertIn("picked by release year", r["check_note"])

    def test_longer_steam_name_and_dlc_skipped(self):
        r = self.run_row(game="Dying Light 2", developer="Techland", launch_year_approx="2022",
                         launch_type="full")
        self.assertEqual((r["steam_app_id"], r["check_status"]), (534380, "OK"))

    def test_store_shows_1_0_date_is_flagged_and_not_fetchable(self):
        r = self.run_row(game="Against the Storm", developer="Eremite Games",
                         launch_year_approx="2021", launch_type="early_access")
        self.assertEqual(r["check_status"], "CHECK DATE")
        self.assertEqual(r["launch_date"], "")              # fetch_reviews will skip it
        self.assertIn("400 reviews before 2023-12-08", r["check_note"])

    def test_manual_override_fixes_it(self):
        r = self.run_row(game="Against the Storm", developer="Eremite Games", steam_app_id="1336490",
                         launch_year_approx="2021", launch_type="early_access",
                         launch_date_override="2021-11-01")
        self.assertEqual((r["check_status"], r["launch_date"]), ("OK", "2021-11-01"))
        self.assertEqual(r["launch_date_source"], "manual override")

    def test_early_access_needs_confirmation_even_when_date_looks_right(self):
        r = self.run_row(game="Manor Lords", developer="Slavic Magic", launch_year_approx="2024",
                         launch_type="early_access")
        self.assertEqual((r["check_status"], r["launch_date"]), ("NEEDS EA DATE", ""))
        self.assertEqual(r["store_release_date"], "2024-04-26")

    def test_unknown_game_is_not_found(self):
        r = self.run_row(game="Witchfire", developer="The Astronauts", launch_year_approx="2024")
        self.assertEqual(r["check_status"], "NOT FOUND")

    def test_developer_mismatch_is_noted(self):
        r = self.run_row(game="Manor Lords", developer="Someone Else", launch_year_approx="2024",
                         launch_type="full")
        self.assertIn("developer on Steam is 'Slavic Magic'", r["check_note"])

    def test_csv_round_trip_keeps_columns_and_overrides(self):
        path = self.tmp / "games.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(ROW_FIELDS + ["role"])
            w.writerow(["Manor Lords", "Slavic Magic", "", "2024", "early_access", "EA hit"])
        with mock.patch.object(steam_api, "make_session", return_value=self.steam), \
             mock.patch("builtins.print"):
            gai.main(["--csv", str(path)])
            rows = read_csv(path)
            rows[0]["launch_date_override"] = "2024-04-26"   # what Kuba would type
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            gai.main(["--csv", str(path)])
        row = read_csv(path)[0]
        self.assertEqual((row["role"], row["steam_app_id"]), ("EA hit", "1363080"))
        self.assertEqual((row["check_status"], row["launch_date"]), ("OK", "2024-04-26"))


if __name__ == "__main__":
    unittest.main()
