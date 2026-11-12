-- =====================================================================
-- 04  Retention by acquisition segment at key tenure months
-- ---------------------------------------------------------------------
-- Same tenure grid, sliced by how the customer came in. Only customers old
-- enough to be observed at month k are in that k's denominator.
-- Month 13 is included because the annual plan renews at day 365.
-- =====================================================================
CREATE OR REPLACE TABLE segment_retention AS
WITH g AS (
    SELECT t.k, t.is_active, c.*
    FROM tenure_grid t
    JOIN cohort_base c USING (user_id)
    WHERE t.k IN (1, 2, 3, 6, 12, 13)
),
long AS (                                   -- unpivot the segment dimensions
    SELECT 'channel'      AS dimension, acquisition_channel                      AS segment, k, is_active FROM g
    UNION ALL
    SELECT 'promo',        CASE WHEN acquired_with_promo THEN 'promo' ELSE 'full price' END, k, is_active FROM g
    UNION ALL
    SELECT 'first_plan',   first_plan_id,                                         k, is_active FROM g
    UNION ALL
    SELECT 'device',       device,                                                k, is_active FROM g
)
SELECT
    dimension,
    segment,
    k,
    COUNT(*)                                             AS n_observed,
    AVG(CASE WHEN is_active THEN 1.0 ELSE 0 END)         AS retention
FROM long
GROUP BY dimension, segment, k
ORDER BY dimension, segment, k;
