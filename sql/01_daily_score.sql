-- 01_daily_score.sql
-- Q1: Does the first week predict the rest?
--
-- v_daily_score     one row per game per day 0..90: daily, cumulative and rolling 7-day % positive
-- v_week1_vs_day90  one row per game: score after week 1, month 1 and day 90, and the gap
--
-- Definitions (also in the README):
--   week 1  = days 0-6 (the first 7 days), month 1 = days 0-29, day 90 = days 0-90
--   a score is "reliable" when it rests on at least 50 reviews
--   week 1 "predicts" day 90 when the two are within 5 percentage points
--   The day-90 score CONTAINS week 1 (13-56% of a game's reviews), which flatters the
--   match. The stricter test compares week 1 with the reviews written AFTER it
--   (days 7-90): change_week1_to_later / week1_predicts_later.

-- Drop first: CREATE OR REPLACE cannot add or reorder view columns.
DROP VIEW IF EXISTS v_week1_vs_day90 CASCADE;
DROP VIEW IF EXISTS v_daily_score CASCADE;

CREATE VIEW v_daily_score AS
WITH days AS (
    -- every game x every day, so days without reviews still appear (as 0)
    SELECT g.app_id, d.day_n
    FROM games g
    CROSS JOIN generate_series(0, 90) AS d(day_n)
),
daily AS (
    SELECT app_id, day_n,
           COUNT(*)                                AS reviews,
           COUNT(*) FILTER (WHERE voted_up)        AS positive
    FROM reviews
    GROUP BY app_id, day_n
),
filled AS (
    SELECT d.app_id, d.day_n,
           COALESCE(x.reviews, 0)  AS reviews,
           COALESCE(x.positive, 0) AS positive
    FROM days d
    LEFT JOIN daily x USING (app_id, day_n)
),
running AS (
    SELECT f.*,
           SUM(reviews)  OVER w_cum AS cum_reviews,
           SUM(positive) OVER w_cum AS cum_positive,
           SUM(reviews)  OVER w_7d  AS r7_reviews,
           SUM(positive) OVER w_7d  AS r7_positive
    FROM filled f
    WINDOW w_cum AS (PARTITION BY app_id ORDER BY day_n
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
           w_7d  AS (PARTITION BY app_id ORDER BY day_n
                     ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
)
SELECT app_id,
       day_n,
       reviews,
       positive,
       ROUND(100.0 * positive / NULLIF(reviews, 0), 1)          AS pct_positive_day,
       cum_reviews,
       ROUND(100.0 * cum_positive / NULLIF(cum_reviews, 0), 1)  AS pct_positive_cum,
       r7_reviews,
       ROUND(100.0 * r7_positive / NULLIF(r7_reviews, 0), 1)    AS pct_positive_7d,
       r7_reviews >= 50                                         AS r7_reliable
FROM running;


CREATE VIEW v_week1_vs_day90 AS
WITH checkpoints AS (
    SELECT app_id,
           MAX(pct_positive_cum) FILTER (WHERE day_n = 6)  AS pct_week1,
           MAX(cum_reviews)      FILTER (WHERE day_n = 6)  AS reviews_week1,
           MAX(pct_positive_cum) FILTER (WHERE day_n = 29) AS pct_month1,
           MAX(pct_positive_cum) FILTER (WHERE day_n = 90) AS pct_day90,
           MAX(cum_reviews)      FILTER (WHERE day_n = 90) AS reviews_day90,
           ROUND(100.0 * SUM(positive) FILTER (WHERE day_n >= 7)
                 / NULLIF(SUM(reviews) FILTER (WHERE day_n >= 7), 0), 1) AS pct_days7_90,
           SUM(reviews) FILTER (WHERE day_n >= 7)          AS reviews_days7_90
    FROM v_daily_score
    GROUP BY app_id
),
unedited AS (
    -- robustness check: the same day-90 score using only reviews never edited,
    -- because an edited review shows today's verdict, not the launch-time one
    SELECT app_id,
           ROUND(100.0 * AVG(voted_up::int) FILTER (WHERE NOT is_edited), 1) AS pct_day90_unedited,
           ROUND(100.0 * AVG(is_edited::int), 1)                             AS pct_edited
    FROM reviews
    GROUP BY app_id
)
SELECT g.app_id,
       g.game,
       g.cohort,
       g.launch_type,
       c.reviews_week1,
       c.pct_week1,
       c.pct_month1,
       c.pct_day90,
       c.reviews_day90,
       ROUND(c.pct_day90 - c.pct_week1, 1)            AS change_week1_to_day90,
       ABS(c.pct_day90 - c.pct_week1) <= 5            AS week1_predicts_day90,
       c.reviews_week1 >= 50                          AS week1_reliable,
       c.pct_days7_90,
       c.reviews_days7_90,
       ROUND(c.pct_days7_90 - c.pct_week1, 1)         AS change_week1_to_later,
       ABS(c.pct_days7_90 - c.pct_week1) <= 5         AS week1_predicts_later,
       ROUND(100.0 * c.reviews_week1 / c.reviews_day90, 1) AS pct_reviews_in_week1,
       CASE WHEN c.pct_day90 - c.pct_week1 >  5 THEN 'improved'
            WHEN c.pct_day90 - c.pct_week1 < -5 THEN 'declined'
            ELSE 'stable' END                         AS trajectory,
       u.pct_day90_unedited,
       u.pct_edited
FROM games g
JOIN checkpoints c USING (app_id)
JOIN unedited u USING (app_id);
