-- view_checks.sql
-- Cross-checks each view against the raw reviews table, computed a second, simpler way.
-- Every query returns NO ROWS when the views are right. Run after load_db.py.

-- 1. Day-90 cumulative score = plain % positive of all the game's reviews
SELECT 'v_daily_score day 90' AS failed_check, s.app_id
FROM v_daily_score s
JOIN (SELECT app_id, ROUND(100.0 * AVG(voted_up::int), 1) AS pct FROM reviews GROUP BY app_id) r USING (app_id)
WHERE s.day_n = 90 AND s.pct_positive_cum <> r.pct;

-- 2. Daily rows add up to every review collected
SELECT 'v_daily_score total', g.app_id
FROM games g
JOIN (SELECT app_id, SUM(reviews) AS n FROM v_daily_score GROUP BY app_id) s USING (app_id)
WHERE s.n <> g.reviews_collected;

-- 3. On day 6 the rolling 7-day window and the cumulative score cover the same days
SELECT 'rolling = cumulative on day 6', app_id
FROM v_daily_score
WHERE day_n = 6 AND pct_positive_7d IS DISTINCT FROM pct_positive_cum;

-- 4. Week-1 score recomputed straight from reviews
SELECT 'v_week1_vs_day90 week 1', w.app_id
FROM v_week1_vs_day90 w
JOIN (SELECT app_id, ROUND(100.0 * AVG(voted_up::int), 1) AS pct
      FROM reviews WHERE day_n <= 6 GROUP BY app_id) r USING (app_id)
WHERE w.pct_week1 <> r.pct;

-- 5. Playtime buckets add up to all negatives with known playtime
SELECT 'v_playtime_buckets negatives', b.app_id
FROM (SELECT app_id, SUM(negatives) AS n FROM v_playtime_buckets GROUP BY app_id) b
JOIN (SELECT app_id, COUNT(*) AS n FROM reviews
      WHERE NOT voted_up AND playtime_bucket IS NOT NULL GROUP BY app_id) r USING (app_id)
WHERE b.n <> r.n;

-- 6. Markets add up to every review, and excess negatives net to ~0 per game
SELECT 'v_language_score totals', l.app_id
FROM (SELECT app_id, SUM(reviews) AS n, SUM(excess_negatives) AS excess
      FROM v_language_score GROUP BY app_id) l
JOIN games g USING (app_id)
WHERE l.n <> g.reviews_collected OR ABS(l.excess) > 10;   -- rounding only

-- 7. Theme mentions never exceed the reviews analysed
SELECT 'v_theme_by_period mentions', app_id, theme, period
FROM v_theme_by_period
WHERE mentions > negatives_analysed;
