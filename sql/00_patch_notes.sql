-- 00_patch_notes.sql
-- Official posts from each game's launch window (from src/fetch_news.py), with your pick
-- of the first major patch. load_db.py rebuilds this table on every run, including
-- --views-only, so a changed pick in patch_candidates.csv shows up after a views refresh.

DROP TABLE IF EXISTS patch_notes CASCADE;

CREATE TABLE patch_notes (
    app_id              INTEGER     NOT NULL REFERENCES games (app_id),
    post_date           DATE        NOT NULL,
    day_n               SMALLINT    NOT NULL,
    title               TEXT        NOT NULL,
    url                 TEXT,
    notes_length        INTEGER,
    looks_like_patch    BOOLEAN,
    looks_like_hotfix   BOOLEAN,
    is_first_major      BOOLEAN     NOT NULL DEFAULT FALSE   -- pick = 'yes' in the CSV
);
