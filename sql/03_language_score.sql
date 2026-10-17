-- 03_language_score.sql
-- Q3: Which markets are unhappy?
--
-- v_language_market   lookup: Steam language code -> market (also the Power BI dim_language)
-- v_language_score    one row per game per market
-- v_market_summary    one row per market, across the 20 core games
--
-- gap_points        = market % positive minus the game's overall % positive
-- excess_negatives  = negative reviews above what the game's overall rate predicts for
--                     that market's volume. It ranks markets by gap AND size, which is
--                     what a localisation / community priority list needs.

-- Drop first: CREATE OR REPLACE cannot add or reorder view columns.
DROP VIEW IF EXISTS v_market_summary CASCADE;
DROP VIEW IF EXISTS v_language_score CASCADE;
DROP VIEW IF EXISTS v_language_market CASCADE;

CREATE VIEW v_language_market AS
SELECT * FROM (VALUES
    ('english',    'English',              1),
    ('schinese',   'Chinese',              2),
    ('tchinese',   'Chinese',              2),
    ('russian',    'Russian',              3),
    ('german',     'German',               4),
    ('polish',     'Polish',               5),
    ('french',     'French',               6),
    ('koreana',    'Korean',               7),
    ('brazilian',  'Portuguese (Brazil)',  8),
    ('portuguese', 'Portuguese (Brazil)',  8),
    ('spanish',    'Spanish',              9),
    ('latam',      'Spanish',              9),
    ('turkish',    'Turkish',             10),
    ('japanese',   'Japanese',            11),
    ('ukrainian',  'Ukrainian',           12)
) AS m(language, market, market_order);


CREATE VIEW v_language_score AS
WITH tagged AS (
    SELECT r.app_id, r.voted_up,
           COALESCE(m.market, 'Other')      AS market,
           COALESCE(m.market_order, 99)     AS market_order
    FROM reviews r
    LEFT JOIN v_language_market m USING (language)
),
per_market AS (
    SELECT app_id, market, market_order,
           COUNT(*)                            AS reviews,
           COUNT(*) FILTER (WHERE NOT voted_up) AS negatives
    FROM tagged
    GROUP BY app_id, market, market_order
),
with_game AS (
    SELECT p.*,
           SUM(reviews)   OVER (PARTITION BY app_id) AS game_reviews,
           SUM(negatives) OVER (PARTITION BY app_id) AS game_negatives
    FROM per_market p
)
SELECT w.app_id,
       g.game,
       g.cohort,
       w.market,
       w.market_order,
       w.reviews,
       ROUND(100.0 * w.reviews / w.game_reviews, 1)                                AS share_of_reviews,
       ROUND(100.0 * (w.reviews - w.negatives) / w.reviews, 1)                     AS pct_positive,
       ROUND(100.0 * (w.game_reviews - w.game_negatives) / w.game_reviews, 1)      AS game_pct_positive,
       ROUND(100.0 * (w.reviews - w.negatives) / w.reviews
           - 100.0 * (w.game_reviews - w.game_negatives) / w.game_reviews, 1)      AS gap_points,
       ROUND(w.negatives - w.reviews * w.game_negatives::numeric / w.game_reviews) AS excess_negatives,
       w.reviews >= 50                                                             AS reliable
FROM with_game w
JOIN games g USING (app_id);


CREATE VIEW v_market_summary AS
SELECT market,
       market_order,
       COUNT(*)                                                         AS core_games_with_50_reviews,
       SUM(reviews)                                                     AS reviews,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gap_points)::numeric, 1) AS median_gap_points,
       COUNT(*) FILTER (WHERE gap_points < -5)                          AS games_more_than_5_below,
       SUM(excess_negatives)                                            AS excess_negatives
FROM v_language_score
WHERE cohort = 'core' AND reliable
GROUP BY market, market_order;
