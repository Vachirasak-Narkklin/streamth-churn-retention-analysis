-- =====================================================================
-- 02  Tenure grid: is each subscriber paying at tenure month k?
-- ---------------------------------------------------------------------
-- Tenure month k is checked on the day  signup_date + 30*k , which lines up
-- with the 30-day billing cycle (month 3 = after the 3rd renewal decision).
--   * Win-backs count: a customer who left and returned is active again.
--   * Censoring: a check day after the 2026-10-31 cut-off is not observed,
--     so that (cohort, k) cell is left out instead of counted as churn.
--   * MRR uses LIST price (annual plan / 12). Promo discounts are excluded on
--     purpose so a discounted first month does not inflate later retention.
-- =====================================================================
CREATE OR REPLACE TABLE tenure_grid AS
WITH params AS (
    SELECT DATE '2026-10-31' AS cutoff
),
months AS (                                  -- tenure months 0..34
    SELECT range AS k FROM range(0, 35)
),
grid AS (
    SELECT c.user_id, c.cohort_month, m.k,
           CAST(c.signup_date + INTERVAL (30 * m.k) DAY AS DATE) AS check_day
    FROM cohort_base c
    CROSS JOIN months m
    CROSS JOIN params
    WHERE c.signup_date + INTERVAL (30 * m.k) DAY <= params.cutoff
)
SELECT
    g.user_id,
    g.cohort_month,
    g.k,
    g.check_day,
    s.sub_id IS NOT NULL                                        AS is_active,
    COALESCE(CASE WHEN p.billing_cycle = 'annual' THEN p.price_thb / 12.0
                  ELSE p.price_thb END, 0)                      AS mrr_thb
FROM grid g
CROSS JOIN params
LEFT JOIN subscriptions s                       -- at most one: subscriptions never overlap
       ON s.user_id = g.user_id
      AND s.start_date <= g.check_day
      AND COALESCE(s.end_date, params.cutoff + INTERVAL 1 DAY) > g.check_day
LEFT JOIN plans p ON p.plan_id = s.plan_id;
