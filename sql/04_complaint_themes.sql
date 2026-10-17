-- 04_complaint_themes.sql
-- Q4: What do players complain about?
--
-- v_complaint_themes   the keyword list per theme (English + Polish stems), in one place
-- mv_review_themes     MATERIALIZED: one row per negative review per theme it mentions
-- v_theme_by_period    one row per game x theme x period (week 1 / days 7-29 / days 30-90)
--
-- Scope: negative reviews written in English or Polish (keywords exist for those two).
-- A review can mention several themes, so shares do not add up to 100%.
-- Keyword matching misses sarcasm and synonyms: treat shares as indicative, not exact.
-- \m = start of a word; patterns are stems, so 'optymaliz' also matches 'optymalizacja'.

-- Drop first: CREATE OR REPLACE cannot add or reorder view columns.
DROP VIEW IF EXISTS v_theme_by_period CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mv_review_themes CASCADE;
DROP VIEW IF EXISTS v_complaint_themes CASCADE;

CREATE VIEW v_complaint_themes AS
SELECT * FROM (VALUES
    ('performance & crashes', 1,
     '\m(fps|frame ?rates?|stutter|lags?\M|laggy|optimi[sz]|performance|crash|freez|wydajno|optymaliz|klatk|przycin|zacina|wywala)'),
    ('bugs', 2,
     '\m(bugs?\M|buggy|glitch|broken|unfinished|unplayable|b[łl][ęe]d|zbugowan|niedoko[ńn]czon|niegrywaln)'),
    ('price & value', 3,
     '\m(price|overpriced|expensive|not worth|full price|refund|money|cen[aeyi]\M|drogo\M|za drog|nie warto|zwrot|pieni[ąa]dz)'),
    ('length & content', 4,
     '\m(short\M|too long|repetitive|grind|boring|content|end ?game|kr[óo]tk|powtarzaln|nudn|zawarto|monoton)'),
    ('story & writing', 5,
     '\m(story|stories|writing|plot|ending|characters?\M|narrative|fabu[łl]|histori|zako[ńn]czeni|posta[cć])'),
    ('servers & online', 6,
     '\m(servers?\M|online|connection|disconnect|matchmaking|always.online|log ?in\M|serwer|po[łl][ąa]cz|roz[łl][ąa]cz)'),
    ('translation', 7,
     '\m(translat|locali[sz]ation|subtitles?\M|t[łl]umacz|przek[łl]ad|polonizac|lokalizac|dubbing|napis)')
) AS t(theme, theme_order, pattern);


CREATE MATERIALIZED VIEW mv_review_themes AS
SELECT t.recommendation_id,
       t.app_id,
       t.language,
       r.day_n,
       th.theme,
       th.theme_order
FROM review_text_negative t
JOIN reviews r USING (recommendation_id)
JOIN v_complaint_themes th ON t.review ~* th.pattern
WHERE t.language IN ('english', 'polish');

CREATE INDEX idx_mv_review_themes_game ON mv_review_themes (app_id, theme);


CREATE VIEW v_theme_by_period AS
WITH periods AS (
    SELECT * FROM (VALUES ('week 1', 1, 0, 6), ('days 7-29', 2, 7, 29), ('days 30-90', 3, 30, 90))
           AS p(period, period_order, day_from, day_to)
),
analysed AS (
    -- denominator: English/Polish negative reviews per game and period
    SELECT t.app_id, p.period,
           COUNT(*) AS negatives_analysed
    FROM review_text_negative t
    JOIN reviews r USING (recommendation_id)
    JOIN periods p ON r.day_n BETWEEN p.day_from AND p.day_to
    WHERE t.language IN ('english', 'polish')
    GROUP BY t.app_id, p.period
),
mentions AS (
    SELECT m.app_id, m.theme, p.period,
           COUNT(*) AS mentions
    FROM mv_review_themes m
    JOIN periods p ON m.day_n BETWEEN p.day_from AND p.day_to
    GROUP BY m.app_id, m.theme, p.period
)
SELECT g.app_id,
       g.game,
       g.cohort,
       th.theme,
       th.theme_order,
       p.period,
       p.period_order,
       COALESCE(a.negatives_analysed, 0)                                       AS negatives_analysed,
       COALESCE(m.mentions, 0)                                                 AS mentions,
       ROUND(100.0 * COALESCE(m.mentions, 0) / NULLIF(a.negatives_analysed, 0), 1) AS pct_of_negatives,
       COALESCE(a.negatives_analysed, 0) >= 50                                 AS reliable
FROM games g
CROSS JOIN v_complaint_themes th
CROSS JOIN periods p
LEFT JOIN analysed a ON a.app_id = g.app_id AND a.period = p.period
LEFT JOIN mentions m ON m.app_id = g.app_id AND m.theme = th.theme AND m.period = p.period;
