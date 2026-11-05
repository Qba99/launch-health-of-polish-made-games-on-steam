"""Offline tests for fetch_news.py (fake news API) and for reading Excel-edited picks.

Run from the repo root:  python -m unittest discover tests -v
"""
import csv
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import fetch_news as fn  # noqa: E402
import steam_api  # noqa: E402

LAUNCH = date(2024, 3, 4)
DAY0 = fn.launch_ts(LAUNCH)


def post(gid, day, title, length=200):
    return {"gid": str(gid), "title": title, "url": f"https://x/{gid}", "date": DAY0 + day * 86400 + 3600,
            "contents": "x" * length, "feedname": fn.OFFICIAL_FEED}


class FakeResponse:
    def __init__(self, body):
        self.status_code, self._body, self.url, self.headers, self.text = 200, body, "fake", {}, ""

    def json(self):
        return self._body


class FakeNews:
    """Returns posts older than `enddate`, newest first, `count` at a time (like Steam)."""

    def __init__(self, posts):
        self.posts, self.calls = sorted(posts, key=lambda p: -p["date"]), []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params))
        older = [p for p in self.posts if p["date"] < params["enddate"]][: params["count"]]
        return FakeResponse({"appnews": {"appid": params["appid"], "newsitems": older}})


class TestFetchNews(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(steam_api, "PAUSE_SECONDS", 0)
        p.start()
        self.addCleanup(p.stop)

    def test_window_and_paging(self):
        posts = ([post(1, -30, "Pre-order now"), post(2, 120, "Patch 2.0")] +
                 [post(100 + i, i % 90, f"Dev diary {i}") for i in range(230)])
        steam = FakeNews(posts)
        items = fn.fetch_window(steam, 1, DAY0, DAY0 + 91 * 86400 - 1)
        self.assertEqual(len(items), 230)               # both out-of-window posts dropped
        self.assertGreaterEqual(len(steam.calls), 3)    # needed several pages of 100

    def test_suggestion_skips_hotfix_and_short_notes(self):
        rows = [fn.classify(p, DAY0) for p in [
            post(1, 0, "Launch day! Patch notes 1.0.1", 5000),   # day 0: launch post, not a fix
            post(2, 2, "Hotfix 1.0.2", 4000),                    # hotfix
            post(3, 5, "Update 1.0.3 is live", 300),             # too short to be major
            post(4, 9, "Patch 1.1 - performance improvements", 6000),
        ]]
        self.assertEqual(fn.suggest_first_major(rows), "4")
        self.assertTrue(rows[1]["looks_like_hotfix"])

    def test_falls_back_to_first_patch_post(self):
        rows = [fn.classify(post(7, 3, "Hotfix 1.0.1", 100), DAY0)]
        self.assertEqual(fn.suggest_first_major(rows), "7")

    def test_polish_patch_titles(self):
        self.assertTrue(fn.classify(post(8, 4, "Aktualizacja 1.2 – poprawki"), DAY0)["looks_like_patch"])

    def test_excel_saved_picks_survive_rerun(self):
        tmp = Path(tempfile.mkdtemp()) / "patch_candidates.csv"
        # what Polish Excel writes back: semicolons, dd.mm.yyyy, gid in scientific notation
        with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(fn.COLUMNS)
            w.writerow(["1684350", "The Thaumaturge", "04.03.2024", "9", "13.03.2024",
                        "Patch 1.1  - performance", "https://x/4", "6000", "PRAWDA", "FAŁSZ",
                        "yes", "5,24E+18"])
        with mock.patch.object(fn, "CANDIDATES", tmp):
            picks = fn.read_existing_picks()
        fresh = {"app_id": 1684350, **fn.classify(post(4, 9, "Patch 1.1 - performance", 6000), DAY0)}
        self.assertIn(fn.pick_key(fresh), picks)


if __name__ == "__main__":
    unittest.main()
