-- 06  Renewal snapshots: what did a customer look like 21 days before a renewal?
--
-- One row per monthly-plan subscription per renewal decision (end of each
-- 30-day period). Behaviour is measured on the snapshot day, 21 days before
-- the renewal. Customers request a cancellation at most 20 days before the
-- period ends, so nothing after the snapshot can leak into the features.
-- The 21 days are also the lead time a retention team would have to act.

CREATE OR REPLACE TABLE renewal_snapshots AS
WITH params AS (SELECT DATE '2026-10-31' AS cutoff),
first_sub AS (
    SELECT user_id, MIN(start_date) AS first_start FROM subscriptions GROUP BY user_id
),
renewals AS (
    SELECT
        s.sub_id, s.user_id, s.plan_id, s.start_date,
        k.range                                                  AS k,
        CAST(s.start_date + INTERVAL (30 * k.range) DAY AS DATE) AS renewal_day,
        CAST(s.start_date + INTERVAL (30 * k.range - 21) DAY AS DATE) AS snapshot_day,
        -- COALESCE: end_date is NULL for active subscriptions, which must be FALSE
        COALESCE(s.end_date = CAST(s.start_date + INTERVAL (30 * k.range) DAY AS DATE)
            AND s.cancel_type = 'voluntary', FALSE)              AS churned,
        -- payment failures are a billing problem, not a behaviour signal;
        -- flagged so the analysis can leave them out
        COALESCE(s.end_date = CAST(s.start_date + INTERVAL (30 * k.range) DAY AS DATE)
            AND s.cancel_type = 'involuntary', FALSE)            AS ended_involuntary,
        s.start_date = f.first_start                             AS is_first_sub
    FROM subscriptions s
    JOIN plans p USING (plan_id)
    JOIN first_sub f USING (user_id)
    CROSS JOIN range(1, 40) k
    CROSS JOIN params
    WHERE p.billing_cycle = 'monthly'
      AND s.start_date + INTERVAL (30 * k.range) DAY <= params.cutoff
      AND s.start_date + INTERVAL (30 * k.range) DAY <= COALESCE(s.end_date, params.cutoff)
),
-- viewing in the 30 days before the snapshot, inside this subscription only
views AS (
    SELECT r.sub_id, r.k, v.start_ts, v.title_id, v.minutes,
           DATEDIFF('day', CAST(v.start_ts AS DATE), r.snapshot_day) AS days_before
    FROM renewals r
    JOIN viewing_sessions v
      ON  v.user_id = r.user_id
     AND  v.start_ts >= r.start_date
     AND  v.start_ts <  r.snapshot_day
     AND  v.start_ts >= r.snapshot_day - INTERVAL 30 DAY
),
usage AS (
    SELECT
        sub_id, k,
        COUNT(*) FILTER (WHERE days_before < 14)                     AS sessions_14d,
        COUNT(*) FILTER (WHERE days_before >= 14 AND days_before < 28) AS sessions_prev_14d,
        COUNT(*)                                                       AS sessions_30d,
        SUM(minutes) FILTER (WHERE days_before < 14)                   AS minutes_14d
    FROM views GROUP BY sub_id, k
),
title_counts AS (
    SELECT sub_id, k, title_id, COUNT(*) AS n FROM views GROUP BY sub_id, k, title_id
),
top_title AS (                       -- binge signal: share of the single most-watched title
    SELECT sub_id, k, MAX(n) * 1.0 / SUM(n) AS top_title_share
    FROM title_counts GROUP BY sub_id, k
),
last_view AS (                       -- silence: days since last viewing (any time in this sub)
    SELECT r.sub_id, r.k,
           DATEDIFF('day', COALESCE(MAX(CAST(v.start_ts AS DATE)), r.start_date), r.snapshot_day)
               AS days_since_last_view
    FROM renewals r
    LEFT JOIN viewing_sessions v
      ON v.user_id = r.user_id AND v.start_ts >= r.start_date AND v.start_ts < r.snapshot_day
    GROUP BY r.sub_id, r.k, r.start_date, r.snapshot_day
),
tickets AS (
    SELECT r.sub_id, r.k,
           COUNT(t.ticket_id) FILTER (WHERE t.resolved_hours IS NULL)          AS unresolved_total,
           COUNT(t.ticket_id) FILTER (WHERE t.resolved_hours IS NULL
                                        AND t.created_at >= r.snapshot_day - INTERVAL 60 DAY) AS unresolved_60d,
           COUNT(t.ticket_id) FILTER (WHERE t.created_at >= r.snapshot_day - INTERVAL 30 DAY) AS tickets_30d
    FROM renewals r
    LEFT JOIN support_tickets t
      ON t.user_id = r.user_id AND t.created_at < r.snapshot_day
    GROUP BY r.sub_id, r.k
)
SELECT
    r.sub_id, r.user_id, r.k, r.renewal_day, r.snapshot_day, r.plan_id,
    r.is_first_sub,
    c.acquired_with_promo AND r.is_first_sub                AS promo_first_sub,
    c.acquisition_channel, c.age_band,
    COALESCE(u.sessions_14d, 0)                             AS sessions_14d,
    COALESCE(u.sessions_prev_14d, 0)                        AS sessions_prev_14d,
    COALESCE(u.sessions_30d, 0)                             AS sessions_30d,
    COALESCE(u.minutes_14d, 0)                              AS minutes_14d,
    -- usage change: last 14 days vs the 14 before (NULL when there is no baseline)
    CASE WHEN COALESCE(u.sessions_prev_14d, 0) > 0
         THEN COALESCE(u.sessions_14d, 0) * 1.0 / u.sessions_prev_14d END AS usage_ratio_14d,
    tt.top_title_share,
    lv.days_since_last_view,
    tk.unresolved_total, tk.unresolved_60d, tk.tickets_30d,
    r.churned,
    r.ended_involuntary
FROM renewals r
JOIN cohort_base c        USING (user_id)
LEFT JOIN usage u         ON u.sub_id = r.sub_id AND u.k = r.k
LEFT JOIN top_title tt    ON tt.sub_id = r.sub_id AND tt.k = r.k
LEFT JOIN last_view lv    ON lv.sub_id = r.sub_id AND lv.k = r.k
LEFT JOIN tickets tk      ON tk.sub_id = r.sub_id AND tk.k = r.k;
