-- =====================================================================
-- 01  Cohort base: one row per subscriber with the attributes we segment by
-- ---------------------------------------------------------------------
-- Cohort      = calendar month of the first paid subscription (signup).
-- First plan  = plan of the first subscription (win-backs may switch plan,
--               but the cohort is defined by how the customer came in).
-- =====================================================================
CREATE OR REPLACE TABLE cohort_base AS
WITH first_sub AS (
    SELECT
        s.user_id,
        -- FIRST_VALUE picks the plan of the earliest subscription per user
        FIRST_VALUE(s.plan_id) OVER (
            PARTITION BY s.user_id ORDER BY s.start_date
        ) AS first_plan_id,
        ROW_NUMBER() OVER (PARTITION BY s.user_id ORDER BY s.start_date) AS rn,
        COUNT(*)     OVER (PARTITION BY s.user_id)                       AS n_subscriptions
    FROM subscriptions s
)
SELECT
    u.user_id,
    u.signup_date,
    DATE_TRUNC('month', u.signup_date)            AS cohort_month,
    u.acquisition_channel,
    u.acquired_with_promo,
    u.device,
    u.region,
    u.age_band,
    f.first_plan_id,
    p.billing_cycle                                AS first_billing_cycle,
    f.n_subscriptions > 1                          AS ever_won_back
FROM users u
JOIN first_sub f ON f.user_id = u.user_id AND f.rn = 1
JOIN plans p     ON p.plan_id = f.first_plan_id;
