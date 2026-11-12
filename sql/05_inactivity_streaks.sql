-- =====================================================================
-- 05  Viewing gaps and binge streaks (gaps-and-islands)
-- ---------------------------------------------------------------------
-- Step 1  daily viewing per subscription (a win-back is a new subscription,
--         so gaps never span the time a customer was away)
-- Step 2  LAG  -> days since the previous viewing day  (= gaps)
-- Step 3  running SUM of "new streak" flags -> island id (= consecutive days)
-- Step 4  rolling 7-day average of minutes watched (RANGE window on dates)
-- Output  one row per subscription with behaviour features used later for
--         early-warning triggers.
-- =====================================================================
CREATE OR REPLACE TABLE daily_viewing AS
WITH params AS (SELECT DATE '2026-10-31' AS cutoff)
SELECT
    s.sub_id,
    s.user_id,
    CAST(v.start_ts AS DATE)    AS view_day,
    COUNT(*)                    AS sessions,
    SUM(v.minutes)              AS minutes
FROM viewing_sessions v
JOIN subscriptions s
  ON  s.user_id = v.user_id
  AND CAST(v.start_ts AS DATE) >= s.start_date
  AND CAST(v.start_ts AS DATE) <  COALESCE(s.end_date, (SELECT cutoff FROM params) + INTERVAL 1 DAY)
GROUP BY s.sub_id, s.user_id, CAST(v.start_ts AS DATE);


CREATE OR REPLACE TABLE viewing_islands AS
WITH lagged AS (
    SELECT
        d.*,
        LAG(view_day) OVER (PARTITION BY sub_id ORDER BY view_day)            AS prev_day
    FROM daily_viewing d
),
flagged AS (
    SELECT
        *,
        DATEDIFF('day', prev_day, view_day)                                   AS gap_days,
        -- a new island starts when the previous viewing day is not yesterday
        CASE WHEN prev_day IS NULL OR DATEDIFF('day', prev_day, view_day) > 1
             THEN 1 ELSE 0 END                                                AS new_island
    FROM lagged
)
SELECT
    *,
    SUM(new_island) OVER (PARTITION BY sub_id ORDER BY view_day
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)   AS island_id,
    -- rolling 7 calendar days (inclusive), robust to missing days
    SUM(minutes) OVER (PARTITION BY sub_id ORDER BY view_day
                       RANGE BETWEEN INTERVAL 6 DAY PRECEDING AND CURRENT ROW) / 7.0
                                                                              AS minutes_7d_avg
FROM flagged;


CREATE OR REPLACE TABLE subscription_activity AS
WITH params AS (SELECT DATE '2026-10-31' AS cutoff),
islands AS (
    SELECT sub_id, island_id, COUNT(*) AS streak_days
    FROM viewing_islands
    GROUP BY sub_id, island_id
),
per_sub AS (
    SELECT
        sub_id,
        COUNT(*)                                                   AS active_days,
        MAX(view_day)                                              AS last_view_day,
        MAX(gap_days) - 1                                          AS longest_gap_days,
        SUM(CASE WHEN gap_days - 1 >= 14 THEN 1 ELSE 0 END)        AS gaps_14d_plus,
        AVG(minutes)                                               AS avg_minutes_per_active_day
    FROM viewing_islands
    GROUP BY sub_id
)
SELECT
    s.sub_id,
    s.user_id,
    s.status,
    s.cancel_type,
    COALESCE(p.active_days, 0)                                     AS active_days,
    p.longest_gap_days,
    COALESCE(p.gaps_14d_plus, 0)                                   AS gaps_14d_plus,
    (SELECT MAX(streak_days) FROM islands i WHERE i.sub_id = s.sub_id) AS longest_streak_days,
    p.avg_minutes_per_active_day,
    -- silence at the end: days between last viewing day and the end of the
    -- subscription (or the cut-off if still active)
    DATEDIFF('day', COALESCE(p.last_view_day, s.start_date),
             COALESCE(s.end_date, (SELECT cutoff FROM params)))     AS days_silent_at_end
FROM subscriptions s
LEFT JOIN per_sub p ON p.sub_id = s.sub_id;
