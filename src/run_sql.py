"""
Run the SQL pipeline in sql/ against the raw CSVs with DuckDB.

    python src/run_sql.py

Creates data/processed/streamth.duckdb (git-ignored) and exports the
analysis tables to data/processed/*.csv.
"""
from __future__ import annotations

import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"
SQL = ROOT / "sql"
RAW_TABLES = ["users", "plans", "subscriptions", "payments", "viewing_sessions",
              "titles", "support_tickets", "exit_survey"]
EXPORT = ["cohort_base", "retention_matrix", "retention_drop", "retention_curve",
          "segment_retention", "subscription_activity", "renewal_snapshots"]


def connect() -> duckdb.DuckDBPyConnection:
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(OUT / "streamth.duckdb"))
    for t in RAW_TABLES:
        con.execute(f"CREATE OR REPLACE TABLE {t} AS SELECT * FROM read_csv_auto('{RAW / t}.csv')")
    return con


def main():
    con = connect()
    for f in sorted(SQL.glob("*.sql")):
        t0 = time.time()
        con.execute(f.read_text())
        print(f"  {f.name:<28} {time.time() - t0:5.1f}s")
    for t in EXPORT:
        con.execute(f"COPY {t} TO '{OUT / t}.csv' (HEADER)")
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  exported {t:<24} {n:>9,} rows")
    con.close()


if __name__ == "__main__":
    main()
