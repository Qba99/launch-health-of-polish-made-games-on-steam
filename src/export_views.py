"""Phase 4, step 1: export the analysis views to small CSV files.

The notebook reads these files rather than the database, so it runs anywhere:
on Kaggle (upload data/views/ as a dataset), on GitHub's notebook preview, or on
a machine without PostgreSQL. Together they are a few MB, so they are committed.

Usage (from the repo root, after load_db.py):
    python src/export_views.py
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from psycopg2 import sql

import load_db

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "views"
EXPORTS = ["games", "v_daily_score", "v_week1_vs_day90", "v_playtime_buckets",
           "v_negative_profile", "v_language_score", "v_market_summary",
           "v_theme_by_period", "v_first_patch", "v_patch_effect"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("PGHOST", "localhost"))
    parser.add_argument("--port", default=os.environ.get("PGPORT", "5432"))
    parser.add_argument("--user", default=os.environ.get("PGUSER", "postgres"))
    parser.add_argument("--dbname", default="steam_reviews")
    parser.add_argument("--dsn")
    args = parser.parse_args(argv)
    args.password = os.environ.get("PGPASSWORD")
    if not args.dsn and args.password is None:
        args.password = getpass.getpass(f"Password for PostgreSQL user '{args.user}': ")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = load_db.connect(args, args.dbname)
    manifest = {"exported_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "database": conn.info.dbname, "files": {}}
    try:
        with conn.cursor() as cur:
            for name in EXPORTS:
                path = OUT_DIR / f"{name}.csv"
                query = sql.SQL("COPY (SELECT * FROM {}) TO STDOUT WITH (FORMAT csv, HEADER true)").format(
                    sql.Identifier(name))
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    cur.copy_expert(query.as_string(conn), fh)
                cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(name)))
                rows = cur.fetchone()[0]
                manifest["files"][path.name] = rows
                print(f"  {path.name:<28}{rows:>7,} rows")
    finally:
        conn.close()
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"Exported {len(EXPORTS)} views to {OUT_DIR.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
