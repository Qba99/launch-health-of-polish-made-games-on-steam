"""Phase 2, step 2: load the clean CSVs into PostgreSQL and verify the load.

1. creates the database if it does not exist yet (default name: steam_reviews)
2. runs sql/00_schema.sql (drops and recreates the tables, so re-runs are safe)
3. bulk-loads data/clean/*.csv with COPY (seconds, not minutes)
4. creates the analysis views: every sql/NN_*.sql file after 00, in order
5. checks row counts, per-game counts and keys, then prints a summary

Connection settings come from the usual PostgreSQL environment variables
(PGHOST, PGPORT, PGUSER, PGPASSWORD) or the options below. If no password is
set, you are asked for it; it is never written to disk.

Usage (from the repo root):
    python src/load_db.py
    python src/load_db.py --user postgres --dbname steam_reviews
    python src/load_db.py --views-only     # rebuild only the views (after editing sql/)
"""
from __future__ import annotations

import argparse
import csv
import getpass
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql

REPO_ROOT = Path(__file__).resolve().parents[1]
CLEAN_DIR = REPO_ROOT / "data" / "clean"
SQL_DIR = REPO_ROOT / "sql"
SCHEMA_FILE = SQL_DIR / "00_schema.sql"
TABLES = ["games", "reviews", "review_text_negative"]   # load order respects foreign keys


def connect(args, dbname: str):
    if args.dsn:
        return psycopg2.connect(args.dsn)
    return psycopg2.connect(host=args.host, port=args.port, user=args.user,
                            password=args.password, dbname=dbname)


def ensure_database(args) -> None:
    """CREATE DATABASE cannot run inside a transaction, so use autocommit."""
    conn = connect(args, "postgres")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (args.dbname,))
        if cur.fetchone() is None:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(args.dbname)))
            print(f"Created database {args.dbname}")
    conn.close()


def csv_header_and_rows(path: Path) -> tuple[list[str], int]:
    """Header and data-row count (csv-aware, so multi-line review text counts once)."""
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        return header, sum(1 for _ in reader)


def load(conn) -> dict:
    expected = {}
    with conn.cursor() as cur:
        cur.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        for table in TABLES:
            path = CLEAN_DIR / f"{table}.csv"
            header, rows = csv_header_and_rows(path)
            expected[table] = rows
            copy = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')").format(
                sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, header)))
            with open(path, encoding="utf-8", newline="") as fh:
                cur.copy_expert(copy.as_string(conn), fh)
            print(f"  {table:<22}{rows:>10,} rows")
        cur.execute("ANALYZE")
    conn.commit()
    return expected


def apply_views(conn) -> None:
    """Run sql/01_*.sql, 02_*.sql ... in order. Each file is one transaction."""
    files = sorted(f for f in SQL_DIR.glob("[0-9][0-9]_*.sql") if f.name != SCHEMA_FILE.name)
    print("Creating views")
    for path in files:
        with conn.cursor() as cur:
            try:
                cur.execute(path.read_text(encoding="utf-8"))
            except psycopg2.Error as exc:
                conn.rollback()
                raise SystemExit(f"  {path.name} failed: {exc.pgerror or exc}")
        conn.commit()
        print(f"  {path.name}")


def verify(conn, expected: dict) -> bool:
    checks = []
    with conn.cursor() as cur:
        for table, rows in expected.items():
            cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table)))
            got = cur.fetchone()[0]
            checks.append((f"{table}: rows in database = rows in CSV", got == rows, f"{got:,} / {rows:,}"))

        cur.execute("""
            SELECT g.game, g.reviews_collected, COUNT(r.recommendation_id)
            FROM games g LEFT JOIN reviews r USING (app_id)
            GROUP BY g.game, g.reviews_collected
            HAVING COUNT(r.recommendation_id) <> g.reviews_collected""")
        bad = cur.fetchall()
        checks.append(("every game's review count matches clean.py", not bad,
                       ", ".join(f"{g}: {n} vs {c}" for g, c, n in bad) or "23 of 23"))

        cur.execute("""
            SELECT COUNT(*) FROM review_text_negative t
            JOIN reviews r USING (recommendation_id) WHERE r.voted_up""")
        n = cur.fetchone()[0]
        checks.append(("text table holds negative reviews only", n == 0, f"{n} positive found"))

        cur.execute("SELECT COUNT(*) FROM reviews WHERE NOT voted_up")
        negatives = cur.fetchone()[0]
        checks.append(("every negative review has its text row", negatives == expected["review_text_negative"],
                       f"{negatives:,} negative reviews"))

        cur.execute("SELECT MIN(day_n), MAX(day_n) FROM reviews")
        lo, hi = cur.fetchone()
        checks.append(("day_n stays within -7..90", lo >= -7 and hi <= 90, f"{lo}..{hi}"))

        cur.execute("""
            SELECT ROUND(100.0 * AVG(voted_up::int), 1),
                   pg_size_pretty(pg_database_size(current_database()))
            FROM reviews""")
        pct, size = cur.fetchone()

    print("\nChecks")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print(f"\nOverall {pct}% positive across all launch windows; database size {size}")
    return all(ok for _, ok, _ in checks)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("PGHOST", "localhost"))
    parser.add_argument("--port", default=os.environ.get("PGPORT", "5432"))
    parser.add_argument("--user", default=os.environ.get("PGUSER", "postgres"))
    parser.add_argument("--dbname", default="steam_reviews")
    parser.add_argument("--dsn", help="full connection string instead of the options above")
    parser.add_argument("--views-only", action="store_true", help="skip the data load, rebuild views")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args.password = os.environ.get("PGPASSWORD")
    if not args.dsn and args.password is None:
        args.password = getpass.getpass(f"Password for PostgreSQL user '{args.user}': ")

    if args.views_only:
        conn = connect(args, args.dbname)
        try:
            apply_views(conn)
        finally:
            conn.close()
        return 0

    missing = [t for t in TABLES if not (CLEAN_DIR / f"{t}.csv").exists()]
    if missing:
        print("Missing clean files, run src/clean.py first:", ", ".join(missing))
        return 1
    if not args.dsn:
        ensure_database(args)

    conn = connect(args, args.dbname)
    print(f"Loading into {conn.info.dbname} ...")
    try:
        expected = load(conn)
        apply_views(conn)
        ok = verify(conn, expected)
    finally:
        conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
