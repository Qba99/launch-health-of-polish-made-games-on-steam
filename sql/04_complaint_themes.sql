-- 04_complaint_themes.sql
-- Q4: What do players complain about?
--
-- v_complaint_themes   the keyword lists per theme, in one place:
--                      `pattern` for English + Polish, `zh_pattern` for Chinese (simplified + traditional)
-- mv_review_themes     MATERIALIZED: one row per negative review per theme it mentions
-- v_theme_by_period    one row per game x text group x theme x period (week 1 / days 7-29 / days 30-90)
--
-- Scope: negative reviews in English or Polish (text_group 'English/Polish') and in
-- Chinese (text_group 'Chinese'). The two groups are reported side by side, never pooled:
-- different keyword lists catch different amounts, so compare a group with itself over
-- time, and the theme MIX between groups, not raw levels.
-- Chinese has no spaces between words, so zh_pattern uses plain substrings (no \m).
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
     '\m(fps|frame ?rates?|stutter|lags?\M|laggy|optimi[sz]|performance|crash|freez|wydajno|optymaliz|klatk|przycin|zacina|wywala)',
     '(优化|優化|卡顿|卡頓|掉帧|掉幀|帧数|幀數|帧率|幀率|闪退|閃退|崩溃|崩潰|卡死|死机|當機|黑屏|性能|效能|吃配置|配置要求)'),
    ('bugs', 2,
     '\m(bugs?\M|buggy|glitch|broken|unfinished|unplayable|b[łl][ęe]d|zbugowan|niedoko[ńn]czon|niegrywaln)',
     '(bug|漏洞|报错|報錯|半成品|未完成|不完整|进不去|進不去)'),
    ('price & value', 3,
     '\m(price|overpriced|expensive|not worth|full price|refund|money|cen[aeyi]\M|drogo\M|za drog|nie warto|zwrot|pieni[ąa]dz)',
     '(价格|價格|太贵|太貴|不值|退款|史低|打折|原价|原價|定价|定價)'),
    ('length & content', 4,
     '\m(short\M|too long|repetitive|grind|boring|content|end ?game|kr[óo]tk|powtarzaln|nudn|zawarto|monoton)',
     '(内容太少|內容太少|内容少|內容少|内容匮乏|內容匱乏|可玩内容|可玩內容|流程短|太短|重复|重複|无聊|無聊|刷刷刷)'),
    ('story & writing', 5,
     '\m(story|stories|writing|plot|ending|characters?\M|narrative|fabu[łl]|histori|zako[ńn]czeni|posta[cć])',
     '(剧情|劇情|故事|结局|結局|文案|叙事|敘事|角色塑造)'),
    ('servers & online', 6,
     '\m(servers?\M|online|connection|disconnect|matchmaking|always.online|log ?in\M|serwer|po[łl][ąa]cz|roz[łl][ąa]cz)',
     '(服务器|伺服器|联网|聯網|连接|連線|掉线|掉線|断线|斷線|登录|登入|网络|網路|加速器)'),
    ('translation', 7,
     '\m(translat|locali[sz]ation|subtitles?\M|t[łl]umacz|przek[łl]ad|polonizac|lokalizac|dubbing|napis)',
     '(汉化|漢化|翻译|翻譯|机翻|機翻|本地化|中文|字幕|简中|簡中|繁中)')
) AS t(theme, theme_order, pattern, zh_pattern);


CREATE MATERIALIZED VIEW mv_review_themes AS
SELECT t.recommendation_id,
       t.app_id,
       t.language,
       CASE WHEN t.language IN ('english', 'polish') THEN 'English/Polish' ELSE 'Chinese' END AS text_group,
       r.day_n,
       th.theme,
       th.theme_order
FROM review_text_negative t
JOIN reviews r USING (recommendation_id)
JOIN v_complaint_themes th
  ON (t.language IN ('english', 'polish')     AND t.review ~* th.pattern)
  OR (t.language IN ('schinese', 'tchinese')  AND t.review ~* th.zh_pattern);

CREATE INDEX idx_mv_review_themes_game ON mv_review_themes (app_id, text_group, theme);


CREATE VIEW v_theme_by_period AS
WITH periods AS (
    SELECT * FROM (VALUES ('week 1', 1, 0, 6), ('days 7-29', 2, 7, 29), ('days 30-90', 3, 30, 90))
           AS p(period, period_order, day_from, day_to)
),
text_groups AS (
    SELECT * FROM (VALUES ('English/Polish'), ('Chinese')) AS tg(text_group)
),
analysed AS (
    -- denominator: negative reviews per game, text group and period
    SELECT t.app_id,
           CASE WHEN t.language IN ('english', 'polish') THEN 'English/Polish' ELSE 'Chinese' END AS text_group,
           p.period,
           COUNT(*) AS negatives_analysed
    FROM review_text_negative t
    JOIN reviews r USING (recommendation_id)
    JOIN periods p ON r.day_n BETWEEN p.day_from AND p.day_to
    WHERE t.language IN ('english', 'polish', 'schinese', 'tchinese')
    GROUP BY 1, 2, 3
),
mentions AS (
    SELECT m.app_id, m.text_group, m.theme, p.period,
           COUNT(*) AS mentions
    FROM mv_review_themes m
    JOIN periods p ON m.day_n BETWEEN p.day_from AND p.day_to
    GROUP BY 1, 2, 3, 4
)
SELECT g.app_id,
       g.game,
       g.cohort,
       tg.text_group,
       th.theme,
       th.theme_order,
       p.period,
       p.period_order,
       COALESCE(a.negatives_analysed, 0)                                           AS negatives_analysed,
       COALESCE(m.mentions, 0)                                                     AS mentions,
       ROUND(100.0 * COALESCE(m.mentions, 0) / NULLIF(a.negatives_analysed, 0), 1) AS pct_of_negatives,
       COALESCE(a.negatives_analysed, 0) >= 50                                     AS reliable
FROM games g
CROSS JOIN text_groups tg
CROSS JOIN v_complaint_themes th
CROSS JOIN periods p
LEFT JOIN analysed a ON a.app_id = g.app_id AND a.text_group = tg.text_group AND a.period = p.period
LEFT JOIN mentions m ON m.app_id = g.app_id AND m.text_group = tg.text_group
                    AND m.theme = th.theme AND m.period = p.period;
