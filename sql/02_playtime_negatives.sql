-- 02_playtime_negatives.sql
-- Q2: First impression or content problem?
--
-- v_playtime_buckets      one row per game per playtime bucket (long format for Power BI)
-- v_negative_profile      one row per game: where its negative reviews come from
--
-- Playtime = minutes played when the review was written (playtime_at_review_min).
-- 0-2h is Steam's refund window: negatives there point to performance, onboarding
-- or expectations; negatives after 20h+ point to content, endgame or balance.
-- The 20 reviews without playtime are left out of this file.

-- Drop first: CREATE OR REPLACE cannot add or reorder view columns.
DROP VIEW IF EXISTS v_negative_profile CASCADE;
DROP VIEW IF EXISTS v_playtime_buckets CASCADE;

CREATE VIEW v_playtime_buckets AS
WITH base AS (
    SELECT app_id, playtime_bucket, playtime_bucket_order,
           COUNT(*)                                   AS reviews,
           COUNT(*) FILTER (WHERE NOT voted_up)       AS negatives,
           COUNT(*) FILTER (WHERE NOT voted_up AND refunded) AS negatives_refunded
    FROM reviews
    WHERE playtime_bucket IS NOT NULL
    GROUP BY app_id, playtime_bucket, playtime_bucket_order
)
SELECT b.app_id,
       g.game,
       g.cohort,
       b.playtime_bucket,
       b.playtime_bucket_order,
       b.reviews,
       b.negatives,
       ROUND(100.0 * (b.reviews - b.negatives) / b.reviews, 1)                     AS pct_positive,
       ROUND(100.0 * b.negatives / SUM(b.negatives) OVER (PARTITION BY b.app_id), 1) AS share_of_game_negatives,
       b.negatives_refunded,
       b.reviews >= 50                                                             AS reliable
FROM base b
JOIN games g USING (app_id);


CREATE VIEW v_negative_profile AS
WITH per_game AS (
    SELECT app_id,
           COUNT(*) FILTER (WHERE NOT voted_up)                                    AS negatives,
           COUNT(*) FILTER (WHERE NOT voted_up AND playtime_bucket = '0-2h')       AS neg_under_2h,
           COUNT(*) FILTER (WHERE NOT voted_up AND playtime_bucket = '20h+')       AS neg_20h_plus,
           COUNT(*) FILTER (WHERE NOT voted_up AND refunded)                       AS neg_refunded,
           COUNT(*) FILTER (WHERE refunded)                                        AS refunded_all,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY playtime_at_review_min)
               FILTER (WHERE NOT voted_up) / 60.0                                  AS median_hours_negative,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY playtime_at_review_min)
               FILTER (WHERE voted_up) / 60.0                                      AS median_hours_positive
    FROM reviews
    WHERE playtime_bucket IS NOT NULL
    GROUP BY app_id
)
SELECT g.app_id,
       g.game,
       g.cohort,
       p.negatives,
       ROUND(100.0 * p.neg_under_2h / NULLIF(p.negatives, 0), 1)   AS pct_neg_under_2h,
       ROUND(100.0 * p.neg_20h_plus / NULLIF(p.negatives, 0), 1)   AS pct_neg_20h_plus,
       ROUND(100.0 * p.neg_refunded / NULLIF(p.negatives, 0), 1)   AS pct_neg_refunded,
       p.refunded_all,
       ROUND(p.median_hours_negative::numeric, 1)                  AS median_hours_negative,
       ROUND(p.median_hours_positive::numeric, 1)                  AS median_hours_positive,
       CASE WHEN p.neg_under_2h > p.neg_20h_plus THEN 'first impression'
            ELSE 'content' END                                     AS main_problem
FROM games g
JOIN per_game p USING (app_id);
