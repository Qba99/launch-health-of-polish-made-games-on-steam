# Kaggle upload guide

Two items, in this order: the **dataset** first, then the **notebook** attached to it.

## 1. Dataset

**Upload:** the 11 files in `data/views/` (10 CSVs + `manifest.json`, 216 KB). Use Kaggle → Datasets → New Dataset and drag the files in.

**Title:** Polish-made games on Steam: first 90 days

**Subtitle:** 754k Steam reviews from 23 Polish games, aggregated by day, language and playtime

**License:** CC BY 4.0. These are aggregates computed from Steam's public API and contain no review text or reviewer IDs. It's your call, though: "Other (specified in description)" also works.

**Tags:** video games, tabular, exploratory data analysis, data visualization, nlp

**Description** (paste as-is):

> Aggregated launch-window data for 23 games made by Polish studios (2018–2025), built from **754,026 Steam reviews** written between 7 days before and 90 days after each game's launch. It covers all languages and purchase types, and the count is 99.97% of Steam's own.
>
> 20 games form the **core** cohort; Cyberpunk 2077, Dying Light 2 and Frostpunk are **benchmarks**. Every file is an export of a PostgreSQL view, so each row is already aggregated. Reviews were collected with Steam's public `IUserReviewsService/GetAppReviews` API. Reviewer IDs were dropped, and no review text is included.
>
> **Files**
>
> | File | One row per | What it holds |
> | --- | --- | --- |
> | `games.csv` | game | app ID, studio, cohort, launch type and date, genres, lifetime and in-window review counts |
> | `v_daily_score.csv` | game × day 0–90 | daily, cumulative and rolling 7-day % positive |
> | `v_week1_vs_day90.csv` | game | week 1 vs days 7–90 vs day 90 scores, change, last-30-days score, % edited |
> | `v_playtime_buckets.csv` | game × playtime bucket (0–2 h, 2–5 h, 5–20 h, 20 h+) | % positive, share of negatives, refunded negatives |
> | `v_negative_profile.csv` | game | where the negatives sit: under 2 h, 20 h+, refunded, median hours |
> | `v_language_score.csv` | game × review language | % positive, gap to the game's own score, excess negatives |
> | `v_market_summary.csv` | language | median gap, games more than 5 points below, total excess negatives (core games) |
> | `v_theme_by_period.csv` | game × text group × complaint theme × period | share of negatives mentioning each keyword theme (EN/PL and Chinese keywords) |
> | `v_first_patch.csv` | game | the first major patch, picked by hand from Steam announcements |
> | `v_patch_effect.csv` | game | 14 days before vs after that patch: % positive, performance and bug complaints |
>
> Booleans are exported by PostgreSQL as `t`/`f`. `reliable` flags rows with enough reviews to read (usually 50+).
>
> **Caveats:** Steam PC only; verdicts are as reviewers left them today (19.9% were edited); keyword themes are indicative.
>
> Full pipeline (Python → PostgreSQL → Power BI), SQL and data-quality log: https://github.com/Qba99/launch-health-of-polish-made-games-on-steam

Optional: Kaggle's usability score goes up if you add a short description to each column in the **Data** tab. The headers are fairly self-explanatory, so this can wait.

## 2. Notebook

1. Open the dataset page → **New Notebook** → File → **Import Notebook** → upload `notebooks/analysis.ipynb`.
2. Check the right-hand panel: the dataset should be listed under **Input**. If it isn't, use **Add Input** to attach it.
3. **Run All.** The first cell should print `Loaded 10 views from /kaggle/input/... · 23 games, 754,026 reviews`.
4. **Save Version → Save & Run All (Commit)** so the public version shows its outputs.
5. Set it to **Public**. Title: *Launch health of Polish-made games on Steam*.

The notebook finds the data anywhere under `/kaggle/input`. On Kaggle it skips saving the chart PNGs and only shows them inline.
