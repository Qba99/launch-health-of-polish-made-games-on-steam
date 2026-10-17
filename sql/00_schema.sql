-- 00_schema.sql
-- Tables for the Steam launch-health project (PostgreSQL 13+).
-- load_db.py runs this file, then fills the tables from data/clean/*.csv.
-- Re-running it rebuilds everything from scratch, so a load is always repeatable.

DROP TABLE IF EXISTS review_text_negative;
DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS games;

-- One row per game: the 23 titles and how their launch window was defined.
CREATE TABLE games (
    app_id              INTEGER     PRIMARY KEY,           -- Steam app ID
    game                TEXT        NOT NULL,
    developer           TEXT        NOT NULL,
    steam_name          TEXT,
    cohort              TEXT        NOT NULL CHECK (cohort IN ('core', 'benchmark')),
    launch_type         TEXT        NOT NULL CHECK (launch_type IN ('full', 'early_access')),
    launch_date         DATE        NOT NULL,              -- day 0 of the window
    launch_date_source  TEXT,                              -- 'steam store' or 'manual override'
    genres              TEXT,
    role                TEXT,                              -- why the game is in the sample
    reviews_lifetime    INTEGER,                           -- Steam's count, all time (Oct 2026)
    reviews_in_window   INTEGER,                           -- Steam's count, days -7..90
    reviews_collected   INTEGER                            -- unique reviews actually downloaded
);

-- One row per review written from 7 days before launch to day 90.
-- No reviewer IDs: they were removed before anything was saved.
CREATE TABLE reviews (
    recommendation_id           BIGINT      PRIMARY KEY,
    app_id                      INTEGER     NOT NULL REFERENCES games (app_id),
    language                    TEXT        NOT NULL,      -- Steam language code, e.g. 'english', 'schinese'
    created_at                  TIMESTAMPTZ NOT NULL,
    updated_at                  TIMESTAMPTZ NOT NULL,
    created_date                DATE        NOT NULL,      -- UTC date the review was written
    day_n                       SMALLINT    NOT NULL,      -- days since launch day; -7..-1 = pre-release
    is_prerelease               BOOLEAN     NOT NULL,
    voted_up                    BOOLEAN     NOT NULL,      -- TRUE = recommended (positive)
    is_edited                   BOOLEAN     NOT NULL,      -- updated after it was written
    votes_up                    INTEGER,                   -- 'helpful' votes
    votes_funny                 INTEGER,
    weighted_vote_score         REAL,
    comment_count               INTEGER,
    steam_purchase              BOOLEAN,                   -- FALSE = key, gift or other source
    received_for_free           BOOLEAN,
    written_during_early_access BOOLEAN,
    primarily_steam_deck        BOOLEAN,
    refunded                    BOOLEAN,
    has_dev_response            BOOLEAN     NOT NULL,
    author_num_reviews          INTEGER,
    playtime_at_review_min      INTEGER,                   -- minutes played when the review was written
    playtime_forever_min        INTEGER,                   -- minutes played in total (as of download)
    playtime_bucket             TEXT        CHECK (playtime_bucket IN ('0-2h', '2-5h', '5-20h', '20h+')),
    playtime_bucket_order       SMALLINT,                  -- 1..4, for sorting in Power BI
    review_length               INTEGER                    -- characters in the review text
);

CREATE INDEX idx_reviews_game_day ON reviews (app_id, day_n);
CREATE INDEX idx_reviews_language ON reviews (language);

-- Text of negative reviews only (enough for complaint themes, Q4).
CREATE TABLE review_text_negative (
    recommendation_id   BIGINT      PRIMARY KEY REFERENCES reviews (recommendation_id),
    app_id              INTEGER     NOT NULL REFERENCES games (app_id),
    language            TEXT        NOT NULL,
    review              TEXT
);
