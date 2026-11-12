"""
Checks for the SQL cohort pipeline (run src/run_sql.py first).

1. Retention computed in SQL matches an independent pandas calculation.
2. Completeness rule: no partly observed (cohort, month) cell slips through.
3. Basic sanity: month 0 is 100%, retention never rises above 100%, and
   GRR never exceeds NRR.
"""
from pathlib import Path

import duckdb
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "streamth.duckdb"
RAW = ROOT / "data" / "raw"
CUTOFF = pd.Timestamp("2026-10-31")

pytestmark = pytest.mark.skipif(not DB.exists(), reason="run src/run_sql.py first")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(DB), read_only=True)
    yield c
    c.close()


@pytest.mark.parametrize("cohort", ["2024-01", "2025-06"])
def test_sql_matches_pandas(con, cohort):
    users = pd.read_csv(RAW / "users.csv", parse_dates=["signup_date"])
    subs = pd.read_csv(RAW / "subscriptions.csv", parse_dates=["start_date", "end_date"])
    coh = users[users.signup_date.dt.to_period("M") == pd.Period(cohort)]
    subs = subs[subs.user_id.isin(coh.user_id)]
    for k in (1, 3, 12):
        check = (coh.set_index("user_id").signup_date + pd.Timedelta(days=30 * k)).rename("check")
        m = subs.merge(check, left_on="user_id", right_index=True)
        active = m[(m.start_date <= m.check)
                   & (m.end_date.fillna(CUTOFF + pd.Timedelta(days=1)) > m.check)].user_id.nunique()
        expected = active / len(coh)
        got = con.execute("SELECT logo_retention FROM retention_matrix "
                          "WHERE cohort_month = ? AND k = ?", [f"{cohort}-01", k]).fetchone()[0]
        assert got == pytest.approx(expected, abs=1e-9), (cohort, k)


def test_only_complete_cells(con):
    # last signup day of the cohort + 30k must be on/before the cut-off
    bad = con.execute("""
        SELECT COUNT(*) FROM retention_matrix
        WHERE (cohort_month + INTERVAL 1 MONTH - INTERVAL 1 DAY) + INTERVAL (30 * k) DAY
              > DATE '2026-10-31'""").fetchone()[0]
    assert bad == 0


def test_sanity(con):
    assert con.execute("SELECT MIN(logo_retention), MAX(logo_retention) "
                       "FROM retention_matrix WHERE k = 0").fetchone() == (1.0, 1.0)
    assert con.execute("SELECT MAX(logo_retention) FROM retention_matrix").fetchone()[0] <= 1.0
    assert con.execute("SELECT COUNT(*) FROM retention_matrix WHERE grr > nrr + 1e-9").fetchone()[0] == 0
