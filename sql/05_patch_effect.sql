-- 05_patch_effect.sql
-- Q4, second half: do complaints fall after the first major patch?
--
-- v_first_patch    one row per game that has a patch picked (pick = yes in patch_candidates.csv)
-- v_patch_effect   one row per game: 14 days before vs 14 days after that patch
--
-- The patch day itself is left out of both sides (notes and reviews cross over that day).
-- Windows are clipped to days 0-90, so an early patch has a shorter "before" side:
-- days_before / days_after show the real length, and `reliable` needs 50+ reviews on each side.
-- Read the change with care: scores also drift naturally after launch (see v_daily_score),
-- so a patch effect is a before/after comparison, not proof of cause.

DROP VIEW IF EXISTS v_patch_effect CASCADE;
DROP VIEW IF EXISTS v_first_patch CASCADE;

CREATE VIEW v_first_patch AS
SELECT DISTINCT ON (p.app_id)
       p.app_id,
       g.game,
       p.day_n                                               AS patch_day,
       p.post_date                                           AS patch_date,
       p.title                                               AS patch_title,
       p.url                                                 AS patch_url,
       COUNT(*) OVER (PARTITION BY p.app_id)                 AS picks_marked   -- should be 1
FROM patch_notes p
JOIN games g USING (app_id)
WHERE p.is_first_major
ORDER BY p.app_id, p.day_n;          -- if several rows say yes, the earliest wins


CREATE VIEW v_patch_effect AS
WITH win AS (
    SELECT app_id, game, patch_day, patch_title,
           GREATEST(0,  patch_day - 14) AS before_from, patch_day - 1  AS before_to,
           patch_day + 1 AS after_from,  LEAST(90, patch_day + 14)      AS after_to
    FROM v_first_patch
),
scores AS (
    SELECT w.app_id,
           COUNT(*)     FILTER (WHERE r.day_n BETWEEN w.before_from AND w.before_to)                  AS reviews_before,
           COUNT(*)     FILTER (WHERE r.day_n BETWEEN w.before_from AND w.before_to AND r.voted_up)   AS positive_before,
           COUNT(*)     FILTER (WHERE r.day_n BETWEEN w.after_from AND w.after_to)                    AS reviews_after,
           COUNT(*)     FILTER (WHERE r.day_n BETWEEN w.after_from AND w.after_to AND r.voted_up)     AS positive_after
    FROM win w
    JOIN reviews r USING (app_id)
    GROUP BY w.app_id
),
analysed AS (       -- English/Polish negative reviews: the base for theme shares
    SELECT w.app_id,
           COUNT(*) FILTER (WHERE r.day_n BETWEEN w.before_from AND w.before_to) AS neg_before,
           COUNT(*) FILTER (WHERE r.day_n BETWEEN w.after_from AND w.after_to)   AS neg_after
    FROM win w
    JOIN review_text_negative t USING (app_id)
    JOIN reviews r USING (recommendation_id)
    WHERE t.language IN ('english', 'polish')
    GROUP BY w.app_id
),
themes AS (
    SELECT w.app_id,
           COUNT(*) FILTER (WHERE m.theme = 'performance & crashes' AND m.day_n BETWEEN w.before_from AND w.before_to) AS perf_before,
           COUNT(*) FILTER (WHERE m.theme = 'performance & crashes' AND m.day_n BETWEEN w.after_from AND w.after_to)   AS perf_after,
           COUNT(*) FILTER (WHERE m.theme = 'bugs' AND m.day_n BETWEEN w.before_from AND w.before_to)                  AS bugs_before,
           COUNT(*) FILTER (WHERE m.theme = 'bugs' AND m.day_n BETWEEN w.after_from AND w.after_to)                    AS bugs_after
    FROM win w
    JOIN mv_review_themes m USING (app_id)
    WHERE m.text_group = 'English/Polish'      -- same base as `analysed`
    GROUP BY w.app_id
)
SELECT w.app_id,
       w.game,
       w.patch_day,
       w.patch_title,
       w.before_to - w.before_from + 1                                             AS days_before,
       w.after_to - w.after_from + 1                                               AS days_after,
       s.reviews_before,
       ROUND(100.0 * s.positive_before / NULLIF(s.reviews_before, 0), 1)           AS pct_positive_before,
       s.reviews_after,
       ROUND(100.0 * s.positive_after / NULLIF(s.reviews_after, 0), 1)             AS pct_positive_after,
       ROUND(100.0 * s.positive_after / NULLIF(s.reviews_after, 0)
           - 100.0 * s.positive_before / NULLIF(s.reviews_before, 0), 1)           AS change_points,
       ROUND(100.0 * COALESCE(t.perf_before, 0) / NULLIF(a.neg_before, 0), 1)      AS pct_neg_performance_before,
       ROUND(100.0 * COALESCE(t.perf_after, 0)  / NULLIF(a.neg_after, 0), 1)       AS pct_neg_performance_after,
       ROUND(100.0 * COALESCE(t.bugs_before, 0) / NULLIF(a.neg_before, 0), 1)      AS pct_neg_bugs_before,
       ROUND(100.0 * COALESCE(t.bugs_after, 0)  / NULLIF(a.neg_after, 0), 1)       AS pct_neg_bugs_after,
       s.reviews_before >= 50 AND s.reviews_after >= 50                            AS reliable
FROM win w
JOIN scores s USING (app_id)
LEFT JOIN analysed a USING (app_id)
LEFT JOIN themes t USING (app_id);
