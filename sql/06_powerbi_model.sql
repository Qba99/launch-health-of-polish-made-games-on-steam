-- 06_powerbi_model.sql
-- Star schema for the Power BI dashboard. Import these views (Get Data > PostgreSQL),
-- drop the pbi_ prefix in Power BI, and relate them as listed below.
--
--   fact_reviews  (1 row per review)            -> dim_game, dim_day, dim_date, dim_language, dim_playtime
--   fact_themes   (1 row per review x theme)    -> dim_game, dim_day, dim_language, dim_theme
--   patch_effect  (1 row per game, precomputed) -> dim_game
--
-- The facts carry only the columns the dashboard uses, which keeps the model small.

DROP VIEW IF EXISTS pbi_fact_reviews, pbi_fact_themes, pbi_dim_game, pbi_dim_day, pbi_dim_date,
                    pbi_dim_language, pbi_dim_playtime, pbi_dim_theme, pbi_patch_effect CASCADE;

CREATE VIEW pbi_fact_reviews AS
SELECT recommendation_id,
       app_id,
       day_n,
       created_date,
       language,
       voted_up,
       is_edited,
       refunded,
       steam_purchase,
       written_during_early_access,
       has_dev_response,
       COALESCE(playtime_bucket_order, 0)                 AS playtime_bucket_order,  -- 0 = unknown
       ROUND(playtime_at_review_min / 60.0, 1)            AS playtime_at_review_hours,
       review_length
FROM reviews;

CREATE VIEW pbi_fact_themes AS
SELECT recommendation_id, app_id, day_n, language, theme_order
FROM mv_review_themes;

CREATE VIEW pbi_dim_game AS
SELECT g.app_id,
       g.game,
       g.developer,
       g.cohort,
       g.launch_type,
       g.launch_date,
       EXTRACT(YEAR FROM g.launch_date)::int               AS launch_year,
       g.genres,
       g.reviews_lifetime,
       g.reviews_collected,
       ROUND(100.0 * g.reviews_collected / NULLIF(g.reviews_lifetime, 0), 1) AS pct_lifetime_in_window,
       p.patch_day,
       p.patch_title
FROM games g
LEFT JOIN v_first_patch p USING (app_id);

CREATE VIEW pbi_dim_day AS
SELECT d                                                    AS day_n,
       d / 7 + 1                                            AS week_since_launch,
       CASE WHEN d <= 6 THEN 'week 1' WHEN d <= 29 THEN 'days 7-29' ELSE 'days 30-90' END AS period,
       CASE WHEN d <= 6 THEN 1 WHEN d <= 29 THEN 2 ELSE 3 END                          AS period_order,
       CASE WHEN d <= 29 THEN 'month 1' WHEN d <= 60 THEN 'month 2' ELSE 'month 3' END  AS month_since_launch
FROM generate_series(0, 90) AS d;

CREATE VIEW pbi_dim_date AS
SELECT d::date                                              AS date,
       EXTRACT(YEAR FROM d)::int                            AS year,
       EXTRACT(QUARTER FROM d)::int                         AS quarter,
       EXTRACT(MONTH FROM d)::int                           AS month,
       TO_CHAR(d, 'Mon')                                    AS month_name,
       EXTRACT(ISODOW FROM d)::int                          AS weekday,          -- 1 = Monday
       TO_CHAR(d, 'Dy')                                     AS weekday_name
FROM generate_series((SELECT MIN(created_date) FROM reviews),
                     (SELECT MAX(created_date) FROM reviews), interval '1 day') AS d;

CREATE VIEW pbi_dim_language AS
SELECT l.language,
       COALESCE(m.market, 'Other')                          AS market,
       COALESCE(m.market_order, 99)                         AS market_order,
       CASE WHEN l.language IN ('english', 'polish')       THEN 'English/Polish'
            WHEN l.language IN ('schinese', 'tchinese')    THEN 'Chinese' END AS text_group  -- theme keywords exist for these
FROM (SELECT DISTINCT language FROM reviews) l
LEFT JOIN v_language_market m USING (language);

CREATE VIEW pbi_dim_playtime AS
SELECT * FROM (VALUES
    (0, 'unknown', FALSE),
    (1, '0-2h',    TRUE),
    (2, '2-5h',    FALSE),
    (3, '5-20h',   FALSE),
    (4, '20h+',    FALSE)
) AS p(playtime_bucket_order, playtime_bucket, in_refund_window);

CREATE VIEW pbi_dim_theme AS
SELECT theme_order, theme FROM v_complaint_themes;

CREATE VIEW pbi_patch_effect AS
SELECT * FROM v_patch_effect;
