"""Phase 2, step 2: load the clean CSVs into PostgreSQL and verify the load.

1. creates the database if it does not exist yet (default name: steam_reviews)
2. runs sql/00_schema.sql (drops and recreates the tables, so re-runs are safe)
3. bulk-loads data/clean/*.csv with COPY (seconds, not minutes)
4. loads patch_candidates.csv (your first-major-patch picks), if it exists
5. creates the analysis views: every sql/NN_*.sql file after 00, in order
6. checks row counts, per-game counts and keys, then prints a summary

Connection settings come from the usual PostgreSQL environment variables
(PGHOST, PGPORT, PGUSER, PGPASSWORD) or the options below. If no password is
set, you are asked for it; it is never written to disk.

Usage (from the repo root):
    python src/load_db.py
    python src/load_db.py --user postgres --dbname steam_reviews
    python src/load_db.py --views-only     # rebuild only the views (after editing sql/)
Both runs finish by running sql/tests/view_checks.sql and printing PASS/FAIL per check.
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
from psycopg2.extras import execute_values

from fetch_news import CANDIDATES, normalise_date, read_candidates

REPO_ROOT = Path(__file__).resolve().parents[1]
CLEAN_DIR = REPO_ROOT / "data" / "clean"
SQL_DIR = REPO_ROOT / "sql"
SCHEMA_FILE = SQL_DIR / "00_schema.sql"
PATCH_TABLE_FILE = SQL_DIR / "00_patch_notes.sql"
VIEW_CHECKS_FILE = SQL_DIR / "tests" / "view_checks.sql"
TRUE_WORDS = {"true", "1", "yes", "tak", "prawda"}       # Polish Excel writes PRAWDA/FAŁSZ
TABLES = ["games", "reviews", "review_text_negative"]   # load order respects foreign keys


def connect(args, dbname: str):
    # client_encoding UTF8: the SQL files contain Polish and Chinese keywords
    if args.dsn:
        return psycopg2.connect(args.dsn, client_encoding="UTF8")
    return psycopg2.connect(host=args.host, port=args.port, user=args.user,
                            password=args.password, dbname=dbname, client_encoding="UTF8")


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


def load_patches(conn) -> None:
    """(Re)build patch_notes from data/clean/patch_candidates.csv; empty if the file is missing."""
    rows = read_candidates(CANDIDATES)
    with conn.cursor() as cur:
        cur.execute(PATCH_TABLE_FILE.read_text(encoding="utf-8"))
        cur.execute("SELECT app_id FROM games")
        known = {r[0] for r in cur.fetchall()}
        values = []
        for r in rows:
            app_id = int(r["app_id"])
            if app_id not in known:
                continue
            values.append((app_id, normalise_date(r["post_date"]), int(float(r["day_n"])),
                           " ".join(r["title"].split()), r.get("url") or None,
                           int(float(r["notes_length"] or 0)),
                           r["looks_like_patch"].lower() in TRUE_WORDS,
                           r["looks_like_hotfix"].lower() in TRUE_WORDS,
                           r.get("pick", "").lower() == "yes"))
        if values:
            execute_values(cur, """INSERT INTO patch_notes (app_id, post_date, day_n, title, url,
                notes_length, looks_like_patch, looks_like_hotfix, is_first_major) VALUES %s""", values)
    conn.commit()
    picked = len({v[0] for v in values if v[-1]})
    print(f"  patch_notes            {len(values):>10,} rows (first major patch picked for {picked} games)"
          if rows else f"  patch_notes                     0 rows ({CANDIDATES.name} not found yet)")


def apply_views(conn) -> None:
    """Run sql/01_*.sql, 02_*.sql ... in order. Each file is one transaction."""
    files = sorted(f for f in SQL_DIR.glob("[0-9][0-9]_*.sql") if not f.name.startswith("00_"))
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


def run_view_checks(conn) -> bool:
    """Run sql/tests/view_checks.sql here, so no manual pgAdmin step is needed.

    Each check is preceded by a '-- N. description' comment and passes when it returns no rows.
    """
    with conn.cursor() as cur:
        cur.execute("""SELECT table_name FROM information_schema.views WHERE table_schema = 'public'
                       UNION ALL SELECT matviewname FROM pg_matviews WHERE schemaname = 'public'
                       ORDER BY 1""")
        views = [r[0] for r in cur.fetchall()]
        print(f"\nViews in database '{conn.info.dbname}' ({len(views)}): {', '.join(views)}")

        checks, name, lines = [], None, []
        for line in VIEW_CHECKS_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("-- ") and line[3:4].isdigit():
                name = line[3:].strip()
            elif not line.startswith("--"):
                lines.append(line)
                if line.split("--")[0].rstrip().endswith(";"):    # ignore a trailing comment
                    checks.append((name, "\n".join(lines)))
                    name, lines = None, []
        ok_all = True
        print("View checks (each must return no rows)")
        for name, statement in checks:
            try:
                cur.execute(statement)
                bad = cur.rowcount
            except psycopg2.Error as exc:
                conn.rollback()
                bad, name = -1, f"{name}: {exc.pgerror or exc}".strip()
            ok = bad == 0
            ok_all &= ok
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or bad < 0 else f" ({bad} rows)"))
    return ok_all


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
        print(f"Connected to database '{conn.info.dbname}'")
        try:
            load_patches(conn)
            apply_views(conn)
            ok = run_view_checks(conn)
        finally:
            conn.close()
        return 0 if ok else 1

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
        load_patches(conn)
        apply_views(conn)
        ok = verify(conn, expected)
        ok = run_view_checks(conn) and ok
    finally:
        conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
