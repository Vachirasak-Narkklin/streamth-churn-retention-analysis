"""
Data-quality and pattern-recovery tests for the StreamTH synthetic dataset.

Part A – integrity: keys, foreign keys, valid dates, domains, row counts.
Part B – patterns: every planted pattern must be recoverable from the RAW
tables alone (no ground-truth columns), using the metric definitions in
CLAUDE.md. If a pattern test fails, the simulation is not fit for analysis.

Run:  pytest -q
"""
from pathlib import Path

import duckdb
import pytest

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
TABLES = ["users", "plans", "subscriptions", "payments", "viewing_sessions",
          "titles", "support_tickets", "exit_survey"]
CUTOFF = "DATE '2026-10-31'"


@pytest.fixture(scope="session")
def db():
    con = duckdb.connect()
    for t in TABLES:
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM read_csv_auto('{RAW / t}.csv')")
    return con


def one(db, sql):
    return db.execute(sql).fetchone()[0]


# ------------------------------------------------------------ A. integrity
@pytest.mark.parametrize("table,lo,hi", [
    ("users", 45_000, 55_000), ("plans", 4, 4), ("titles", 1_000, 2_000),
    ("subscriptions", 50_000, 60_000), ("payments", 250_000, 500_000),
    ("viewing_sessions", 1_500_000, 4_000_000), ("support_tickets", 20_000, 100_000),
    ("exit_survey", 8_000, 25_000),
])
def test_row_counts(db, table, lo, hi):
    assert lo <= one(db, f"SELECT COUNT(*) FROM {table}") <= hi


@pytest.mark.parametrize("table,key", [
    ("users", "user_id"), ("plans", "plan_id"), ("titles", "title_id"),
    ("subscriptions", "sub_id"), ("payments", "payment_id"),
    ("viewing_sessions", "session_id"), ("support_tickets", "ticket_id"),
])
def test_primary_keys_unique_not_null(db, table, key):
    assert one(db, f"SELECT COUNT(*) - COUNT(DISTINCT {key}) FROM {table}") == 0
    assert one(db, f"SELECT COUNT(*) FROM {table} WHERE {key} IS NULL") == 0


@pytest.mark.parametrize("child,fk,parent,pk", [
    ("subscriptions", "user_id", "users", "user_id"),
    ("subscriptions", "plan_id", "plans", "plan_id"),
    ("payments", "sub_id", "subscriptions", "sub_id"),
    ("viewing_sessions", "user_id", "users", "user_id"),
    ("viewing_sessions", "title_id", "titles", "title_id"),
    ("support_tickets", "user_id", "users", "user_id"),
    ("exit_survey", "user_id", "users", "user_id"),
    ("exit_survey", "sub_id", "subscriptions", "sub_id"),
])
def test_no_orphan_foreign_keys(db, child, fk, parent, pk):
    assert one(db, f"""SELECT COUNT(*) FROM {child} c
                       LEFT JOIN {parent} p ON c.{fk} = p.{pk}
                       WHERE p.{pk} IS NULL""") == 0


def test_every_user_has_a_subscription(db):
    assert one(db, """SELECT COUNT(*) FROM users u
                      WHERE NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.user_id)""") == 0


def test_signup_equals_first_subscription(db):
    assert one(db, """SELECT COUNT(*) FROM users u
                      JOIN (SELECT user_id, MIN(start_date) s FROM subscriptions GROUP BY 1) f USING (user_id)
                      WHERE f.s <> u.signup_date""") == 0


def test_dates_within_observation_window(db):
    assert one(db, f"SELECT COUNT(*) FROM users WHERE signup_date < DATE '2024-01-01' OR signup_date > {CUTOFF}") == 0
    assert one(db, f"SELECT COUNT(*) FROM subscriptions WHERE end_date > {CUTOFF}") == 0
    assert one(db, f"SELECT COUNT(*) FROM viewing_sessions WHERE start_ts >= {CUTOFF} + INTERVAL 1 DAY") == 0
    assert one(db, f"SELECT COUNT(*) FROM payments WHERE payment_date > {CUTOFF} + INTERVAL 3 DAY") == 0


def test_subscription_dates_consistent(db):
    assert one(db, "SELECT COUNT(*) FROM subscriptions WHERE end_date < start_date") == 0
    # active <=> no end date; ended <=> cancel_type set
    assert one(db, """SELECT COUNT(*) FROM subscriptions
                      WHERE (status = 'active') <> (end_date IS NULL)
                         OR (status = 'active') <> (cancel_type IS NULL)""") == 0


def test_no_overlapping_subscriptions(db):
    assert one(db, """SELECT COUNT(*) FROM (
                        SELECT start_date, LAG(end_date) OVER (PARTITION BY user_id ORDER BY start_date) prev_end
                        FROM subscriptions) WHERE start_date <= prev_end""") == 0


def test_sessions_after_title_release(db):
    assert one(db, """SELECT COUNT(*) FROM viewing_sessions v JOIN titles t USING (title_id)
                      WHERE CAST(v.start_ts AS DATE) < t.release_date""") == 0


def test_domains(db):
    assert one(db, "SELECT COUNT(*) FROM subscriptions WHERE status NOT IN ('active','cancelled','payment_failed')") == 0
    assert one(db, "SELECT COUNT(*) FROM payments WHERE status NOT IN ('success','failed') OR amount_thb <= 0") == 0
    assert one(db, "SELECT COUNT(*) FROM viewing_sessions WHERE minutes NOT BETWEEN 1 AND 400") == 0
    assert one(db, "SELECT COUNT(*) FROM titles WHERE origin NOT IN ('TH','KR','CN','US','JP')") == 0
    assert one(db, """SELECT COUNT(*) FROM exit_survey WHERE switched_to NOT IN
                      ('Netflix','Prime Video','Disney+','iQIYI/WeTV','Viu','None','Other')""") == 0


def test_survey_only_for_voluntary_cancellations(db):
    assert one(db, """SELECT COUNT(*) FROM exit_survey e JOIN subscriptions s USING (sub_id)
                      WHERE s.cancel_type <> 'voluntary' OR e.cancel_date > s.end_date""") == 0


# ------------------------------------------------------- B. pattern recovery
HAZARD_SQL = """
WITH first_sub AS (                 -- first subscription, monthly plans only
    SELECT s.*, u.acquired_with_promo AS promo,
           ROW_NUMBER() OVER (PARTITION BY s.user_id ORDER BY s.start_date) AS rn
    FROM subscriptions s JOIN users u USING (user_id) JOIN plans p USING (plan_id)
    WHERE p.billing_cycle = 'monthly'),
tenure AS (
    SELECT promo, end_date IS NOT NULL AS ended,
           FLOOR(DATEDIFF('day', start_date, COALESCE(end_date, {cutoff})) / 30) AS periods
    FROM first_sub WHERE rn = 1)
SELECT k.k,
       SUM(CASE WHEN ended AND periods = k.k THEN 1 ELSE 0 END) * 1.0
     / SUM(CASE WHEN periods >= k.k THEN 1 ELSE 0 END) AS hazard
FROM tenure, (SELECT range AS k FROM range(1, 13)) k
WHERE {filter}
GROUP BY k.k ORDER BY k.k
"""


def hazards(db, flt="TRUE"):
    return dict(db.execute(HAZARD_SQL.format(cutoff=CUTOFF, filter=flt)).fetchall())


def test_p1_churn_peaks_at_month_3(db):
    h = hazards(db)
    assert max(h, key=h.get) == 3
    assert max(h[k] for k in range(7, 13)) < h[3] / 2


def test_p2_promo_users_churn_about_2x_at_month_2(db):
    ratio = hazards(db, "promo")[2] / hazards(db, "NOT promo")[2]
    assert 1.5 <= ratio <= 2.6


def test_p3_activation_improves_retention(db):
    r = dict(db.execute(f"""
        WITH act AS (
            SELECT u.user_id,
                   COUNT(DISTINCT CASE WHEN v.start_ts < u.signup_date + INTERVAL 7 DAY THEN v.title_id END) >= 3 AS activated
            FROM users u LEFT JOIN viewing_sessions v USING (user_id)
            WHERE u.signup_date < {CUTOFF} - INTERVAL 180 DAY
            GROUP BY u.user_id),
        first_sub AS (SELECT user_id, ARG_MIN(end_date, start_date) AS e, MIN(start_date) AS s
                      FROM subscriptions GROUP BY 1)
        SELECT activated,
               AVG(CASE WHEN e IS NULL OR DATEDIFF('day', s, e) >= 180 THEN 1.0 ELSE 0 END)
        FROM act JOIN first_sub USING (user_id) GROUP BY 1""").fetchall())
    assert r[True] - r[False] >= 0.12


def test_p4_usage_decays_before_cancellation(db):
    last14, prior14 = db.execute("""
        WITH c AS (SELECT e.user_id, e.cancel_date d, s.start_date
                   FROM exit_survey e JOIN subscriptions s USING (sub_id)
                   WHERE DATEDIFF('day', s.start_date, e.cancel_date) >= 60)
        SELECT AVG(l14), AVG(p14) FROM (
            SELECT c.user_id, c.d,
                   COUNT(CASE WHEN v.start_ts >= c.d - INTERVAL 14 DAY AND v.start_ts < c.d THEN 1 END) l14,
                   COUNT(CASE WHEN v.start_ts >= c.d - INTERVAL 28 DAY AND v.start_ts < c.d - INTERVAL 14 DAY THEN 1 END) p14
            FROM c LEFT JOIN viewing_sessions v USING (user_id) GROUP BY c.user_id, c.d)""").fetchone()
    assert last14 / prior14 <= 0.80


def test_p5_unresolved_tickets_raise_period_hazard(db):
    # Period-level hazard: one row per subscriber per billing period.
    rows = db.execute(f"""
        WITH s AS (SELECT s.* FROM subscriptions s JOIN plans p USING (plan_id)
                   WHERE p.billing_cycle = 'monthly'),
        per AS (
            SELECT s.user_id, k.k,
                   s.start_date + INTERVAL (30 * k.k) DAY AS p_end,
                   s.cancel_type = 'voluntary'
                   AND s.end_date = CAST(s.start_date + INTERVAL (30 * k.k) DAY AS DATE) AS churned
            FROM s, (SELECT range AS k FROM range(1, 36)) k
            WHERE s.start_date + INTERVAL (30 * k.k) DAY <= COALESCE(s.end_date, {CUTOFF})),
        flag AS (
            SELECT per.*,
                   (SELECT COUNT(*) FROM support_tickets t
                    WHERE t.user_id = per.user_id AND t.resolved_hours IS NULL
                      AND t.created_at < per.p_end) >= 2 AS unres2
            FROM per)
        SELECT CASE WHEN k <= 3 THEN '1-3' WHEN k <= 6 THEN '4-6' ELSE '7+' END AS band,
               unres2, AVG(CASE WHEN churned THEN 1.0 ELSE 0 END)
        FROM flag GROUP BY 1, 2""").fetchall()
    # Pooled comparison is confounded by tenure (long-tenure users accumulate tickets
    # AND churn less - Simpson's paradox), so compare within tenure bands.
    rates = {(band, u): v for band, u, v in rows}
    for band in ("1-3", "4-6", "7+"):
        assert rates[(band, True)] / rates[(band, False)] >= 1.5, band


def test_p7_winback_rate_about_15pct(db):
    rate = one(db, f"""
        WITH ended AS (
            SELECT user_id, end_date,
                   LEAD(start_date) OVER (PARTITION BY user_id ORDER BY start_date) AS next_start
            FROM subscriptions)
        SELECT AVG(CASE WHEN DATEDIFF('day', end_date, next_start) <= 183 THEN 1.0 ELSE 0 END)
        FROM ended WHERE end_date IS NOT NULL AND end_date < {CUTOFF} - INTERVAL 183 DAY""")
    assert 0.10 <= rate <= 0.20


def test_p8_annual_churns_less_and_involuntary_share(db):
    ended = dict(db.execute("""SELECT p.billing_cycle, AVG(CASE WHEN s.end_date IS NOT NULL THEN 1.0 ELSE 0 END)
                               FROM subscriptions s JOIN plans p USING (plan_id) GROUP BY 1""").fetchall())
    assert ended["annual"] < ended["monthly"] / 2
    invol = one(db, """SELECT AVG(CASE WHEN cancel_type = 'involuntary' THEN 1.0 ELSE 0 END)
                       FROM subscriptions WHERE end_date IS NOT NULL""")
    assert 0.15 <= invol <= 0.25


def test_p6_binge_and_leave(db):
    # Share of users whose single top title is >= 50% of sessions in a 30-day window:
    # early voluntary churners (window = 30 days before end) vs users still subscribed
    # at day 90 (window = days 60-90 of tenure).
    churn_share, base_share = db.execute(f"""
        WITH first_sub AS (
            SELECT user_id, MIN(start_date) s, ARG_MIN(end_date, start_date) e,
                   ARG_MIN(cancel_type, start_date) ct
            FROM subscriptions GROUP BY 1),
        w AS (
            SELECT user_id, 'churn' grp, e - INTERVAL 30 DAY ws, CAST(e AS TIMESTAMP) we FROM first_sub
            WHERE ct = 'voluntary' AND DATEDIFF('day', s, e) BETWEEN 60 AND 90
            UNION ALL
            SELECT user_id, 'base', s + INTERVAL 60 DAY, s + INTERVAL 90 DAY FROM first_sub
            WHERE (e IS NULL OR DATEDIFF('day', s, e) > 120) AND s < {CUTOFF} - INTERVAL 120 DAY),
        per_title AS (
            SELECT w.user_id, w.grp, v.title_id, COUNT(*) n
            FROM w JOIN viewing_sessions v ON v.user_id = w.user_id
                 AND v.start_ts >= w.ws AND v.start_ts < w.we
            GROUP BY 1, 2, 3),
        per_user AS (
            SELECT user_id, grp, MAX(n) * 1.0 / SUM(n) top_share, SUM(n) total
            FROM per_title GROUP BY 1, 2)
        SELECT AVG(CASE WHEN grp = 'churn' THEN (top_share >= 0.5)::INT END),
               AVG(CASE WHEN grp = 'base'  THEN (top_share >= 0.5)::INT END)
        FROM per_user WHERE total >= 4""").fetchone()
    assert churn_share >= 2 * base_share
