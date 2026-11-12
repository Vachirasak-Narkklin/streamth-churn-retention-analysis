-- =====================================================================
-- 03  Cohort retention matrix: customers (logo) and revenue (GRR / NRR)
-- ---------------------------------------------------------------------
-- logo_retention  = active customers at month k / cohort size
-- grr             = gross revenue retention: each customer's MRR is capped at
--                   their month-0 MRR, so upgrades cannot hide losses
-- nrr             = net revenue retention: includes up/downgrades on win-back
--
-- Completeness rule: a (cohort, k) cell is kept only when EVERY customer of
-- the cohort has reached month k before the cut-off. A partly observed cell
-- would divide a few early-month signups by the whole cohort and look like a
-- collapse in retention.
-- =====================================================================
CREATE OR REPLACE TABLE retention_matrix AS
WITH base AS (
    SELECT
        t.*,
        -- month-0 MRR of the same customer, carried onto every row
        FIRST_VALUE(t.mrr_thb) OVER (PARTITION BY t.user_id ORDER BY t.k) AS mrr_m0
    FROM tenure_grid t
),
cohort_size AS (
    SELECT cohort_month, COUNT(*) AS n_customers, SUM(mrr_thb) AS mrr_start
    FROM base WHERE k = 0
    GROUP BY cohort_month
),
cells AS (
    SELECT
        b.cohort_month,
        b.k,
        COUNT(*)                                                   AS n_observed,
        SUM(CASE WHEN b.is_active THEN 1 ELSE 0 END)               AS active_customers,
        SUM(LEAST(b.mrr_thb, b.mrr_m0))                            AS mrr_capped,
        SUM(b.mrr_thb)                                             AS mrr_thb
    FROM base b
    GROUP BY b.cohort_month, b.k
)
SELECT
    c.cohort_month,
    c.k,
    cs.n_customers,
    c.active_customers,
    c.active_customers * 1.0 / cs.n_customers                      AS logo_retention,
    c.mrr_capped / cs.mrr_start                                    AS grr,
    c.mrr_thb    / cs.mrr_start                                    AS nrr,
    c.mrr_thb
FROM cells c
JOIN cohort_size cs USING (cohort_month)
WHERE c.n_observed = cs.n_customers          -- completeness rule
ORDER BY c.cohort_month, c.k;


-- Month-over-month view: where in the lifecycle do we lose customers?
--   pp_lost              = percentage points of the ORIGINAL cohort lost this month
--   churn_of_remaining   = share of customers still active last month who are
--                          not active this month (a hazard-style rate)
CREATE OR REPLACE TABLE retention_drop AS
WITH lagged AS (
    SELECT
        cohort_month, k, n_customers, active_customers, logo_retention,
        LAG(logo_retention)   OVER (PARTITION BY cohort_month ORDER BY k) AS prev_retention,
        LAG(active_customers) OVER (PARTITION BY cohort_month ORDER BY k) AS prev_active
    FROM retention_matrix
)
SELECT
    cohort_month, k, logo_retention, prev_retention,
    prev_retention - logo_retention                                AS pp_lost,
    (prev_active - active_customers) * 1.0 / NULLIF(prev_active, 0) AS churn_of_remaining
FROM lagged;


-- Blended curve across all cohorts. Each k only uses cohorts fully observed
-- at k (see completeness rule), so young cohorts never drag it down.
CREATE OR REPLACE TABLE retention_curve AS
SELECT
    k,
    COUNT(*)                                                   AS n_cohorts,
    SUM(n_customers)                                           AS n_customers,
    SUM(active_customers) * 1.0 / SUM(n_customers)             AS logo_retention,
    SUM(grr * n_customers) / SUM(n_customers)                  AS grr,
    SUM(nrr * n_customers) / SUM(n_customers)                  AS nrr
FROM retention_matrix
GROUP BY k
ORDER BY k;
