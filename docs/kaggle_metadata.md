# Kaggle dataset metadata

Copy-paste text for the dataset's Data Card and Settings.

## Image

Upload `images/kaggle_thumbnail.png` (1128 × 568). Use the image/cover option on the Data Card (pencil next to the header image).

## Authors

Add yourself: **Jakub Siuda**. The *Collaborator* field only controls who can edit the dataset. *Authors* is the credit shown in the dataset's citation and DOI, so an empty field means the citation has no author. If Kaggle offers a bio field, a link to your website or GitHub works well there.

## Coverage

| Field | Value |
| --- | --- |
| Temporal coverage start | 2018-04-17 |
| Temporal coverage end | 2025-12-17 |
| Geospatial coverage | Worldwide |

The start is 7 days before the earliest launch (Frostpunk, 2018-04-24). The end is day 90 of the latest launch (Dying Light: The Beast, 2025-09-18). Geospatial is "Worldwide" because reviews come from players everywhere; only the studios are Polish.

## Provenance

**Sources**

```
Steam Web API, accessed October 2026:
- IUserReviewsService/GetAppReviews: reviews (https://partner.steamgames.com/doc/store/getreviews)
- ISteamNews/GetNewsForApp: official patch announcements (https://partner.steamgames.com/doc/webapi/ISteamNews)
- Steam Store appdetails: release dates, genres and lifetime review totals
```

**Collection methodology**

```
Every review written from 7 days before to 90 days after each game's Steam launch (UTC) was downloaded, in all languages and purchase types, with a resumable Python script paced at one request per second. Day 0 is the US store release date, or the Early Access date where that was the real launch (Green Hell, Manor Lords, Chernobylite, Against the Storm). The 754,026 reviews collected match 99.97% of Steam's own count for those windows. Reviewer IDs were dropped before saving.

The reviews were cleaned in Python, loaded into PostgreSQL and aggregated with SQL views; each file here is one of those views. Complaint themes come from keyword patterns (English/Polish and Chinese) matched against negative review text. Each game's first major patch was picked by hand from its official Steam announcements. Review text itself is not included.

Code, SQL and the data-quality log: https://github.com/Qba99/launch-health-of-polish-made-games-on-steam
```

**Citations**

```
Siuda, J. (2026). Launch health of Polish-made games on Steam [Source code and analysis]. GitHub. https://github.com/Qba99/launch-health-of-polish-made-games-on-steam
```

## DOI citation

Kaggle issues the DOI itself; you don't type one in. Look for **Generate DOI** in the DOI Citation section or in Settings. A DOI is permanent and records the author list, so add yourself as author first. Generate it once the metadata is final.

## File descriptions

Paste each one into the file's description in the Data Explorer.

| File | Description |
| --- | --- |
| `games.csv` | One row per game: Steam app ID, studio, cohort (core/benchmark), launch type and date, genres and review counts. Join the other files on `app_id`. |
| `v_daily_score.csv` | One row per game per day (days 0–90): reviews written that day, plus the daily, cumulative and rolling 7-day % positive. |
| `v_week1_vs_day90.csv` | One row per game: week-1 score vs days 7–90 vs day 90, the change between them, and how many reviews were edited later. |
| `v_playtime_buckets.csv` | One row per game per playtime bucket (0–2 h, 2–5 h, 5–20 h, 20 h+) at the time of review: % positive and share of the game's negatives. |
| `v_negative_profile.csv` | One row per game: where its negative reviews sit (under 2 h, 20 h+, refunded) and whether it reads as a first-impression or content problem. |
| `v_language_score.csv` | One row per game per review language (market): % positive, gap to the game's own score and excess negative reviews. |
| `v_market_summary.csv` | One row per market, core games only: median gap, number of games more than 5 points below, and total excess negatives. |
| `v_theme_by_period.csv` | One row per game × text group × complaint theme × period: share of negative reviews mentioning each theme in week 1, days 7–29 and days 30–90. |
| `v_first_patch.csv` | One row per game: its first major patch (day, date, title, link), picked by hand from official Steam announcements. |
| `v_patch_effect.csv` | One row per game: % positive and the share of performance and bug complaints in the 14 days before vs after the first major patch. |
| `manifest.json` | Export timestamp and the row count of every file. |

## Column descriptions

On the Data Card, open a file in the Data Explorer and switch to the **Column** view. Then click the pencil/edit icon by each column, or by the column list, to add a description. Kaggle moves its UI around, so look for a "Columns" or "Edit" option if it isn't exactly there.

Conventions for all files:

- Booleans are `t`/`f`.
- Percentages are 0–100, rounded to 1 decimal.
- "Day" means days since launch (day 0 = launch day, UTC).
- `reliable` is `t` when the figure rests on at least 50 reviews.

### games.csv

| Column | Description |
| --- | --- |
| app_id | Steam app ID; the key used by every other file |
| game | Game name used in this project |
| developer | Polish studio that made the game |
| steam_name | Name on the Steam store today (can include edition suffixes) |
| cohort | `core` (20 games similar in size, used for statistics) or `benchmark` (Cyberpunk 2077, Dying Light 2, Frostpunk) |
| launch_type | `full` release or `early_access` launch |
| launch_date | Day 0 of the window (UTC date) |
| launch_date_source | `steam store` (store release date) or `manual override` (checked by hand, e.g. Early Access date) |
| genres | Steam store genres, separated by semicolons |
| role | Why the game is in the sample |
| reviews_lifetime | Total Steam reviews at collection time (all dates) |
| reviews_in_window | Steam's own count of reviews in the launch window |
| reviews_collected | Reviews actually downloaded for the window |

### v_daily_score.csv

| Column | Description |
| --- | --- |
| app_id | Steam app ID |
| day_n | Days since launch (0–90) |
| reviews | Reviews written that day |
| positive | Positive reviews written that day |
| pct_positive_day | % positive among that day's reviews |
| cum_reviews | Reviews from day 0 up to this day |
| pct_positive_cum | Overall % positive from day 0 up to this day (what the all-time score would show) |
| r7_reviews | Reviews in the last 7 days (this day and the 6 before) |
| pct_positive_7d | Rolling 7-day % positive |
| r7_reliable | `t` when the rolling 7-day window has at least 50 reviews |

### v_week1_vs_day90.csv

| Column | Description |
| --- | --- |
| app_id | Steam app ID |
| game | Game name |
| cohort | `core` or `benchmark` |
| launch_type | `full` or `early_access` |
| reviews_week1 | Reviews written on days 0–6 |
| pct_week1 | % positive on days 0–6 |
| pct_month1 | Overall % positive on days 0–29 |
| pct_day90 | Overall % positive on days 0–90 |
| reviews_day90 | Reviews written on days 0–90 |
| change_week1_to_day90 | pct_day90 minus pct_week1, in points |
| week1_predicts_day90 | `t` when the two are within 5 points (flattered: day 90 includes week 1) |
| week1_reliable | `t` when week 1 has at least 50 reviews |
| pct_days7_90 | % positive among reviews written on days 7–90 only |
| reviews_days7_90 | Reviews written on days 7–90 |
| change_week1_to_later | pct_days7_90 minus pct_week1, in points (no shared reviews) |
| week1_predicts_later | `t` when week 1 and days 7–90 are within 5 points |
| pct_reviews_in_week1 | Share of the 90-day reviews written in week 1 |
| trajectory | `improved` / `declined` when day 90 is more than 5 points above / below week 1, else `stable` |
| pct_day90_unedited | % positive on days 0–90 counting only reviews never edited |
| pct_edited | Share of the 90-day reviews edited after they were written |

### v_playtime_buckets.csv

| Column | Description |
| --- | --- |
| app_id | Steam app ID |
| game | Game name |
| cohort | `core` or `benchmark` |
| playtime_bucket | Hours played when the review was written: `0-2h`, `2-5h`, `5-20h`, `20h+` |
| playtime_bucket_order | Sort order of the bucket (1–4) |
| reviews | Reviews in the bucket |
| negatives | Negative reviews in the bucket |
| pct_positive | % positive in the bucket |
| share_of_game_negatives | Share of the game's 90-day negatives that fall in this bucket |
| negatives_refunded | Negative reviews in the bucket marked as refunded by Steam |
| reliable | `t` when the bucket has at least 50 reviews |

### v_negative_profile.csv

| Column | Description |
| --- | --- |
| app_id | Steam app ID |
| game | Game name |
| cohort | `core` or `benchmark` |
| negatives | Negative reviews on days 0–90 |
| pct_neg_under_2h | Share of negatives written with under 2 hours played (inside Steam's refund window) |
| pct_neg_20h_plus | Share of negatives written with 20+ hours played |
| pct_neg_refunded | Share of negatives marked as refunded |
| refunded_all | All reviews (positive or negative) marked as refunded |
| median_hours_negative | Median hours played at review, negative reviews |
| median_hours_positive | Median hours played at review, positive reviews |
| main_problem | `first impression` when more negatives come under 2 h than at 20 h+, else `content` |

### v_language_score.csv

| Column | Description |
| --- | --- |
| app_id | Steam app ID |
| game | Game name |
| cohort | `core` or `benchmark` |
| market | Review language grouped into a market (Simplified + Traditional Chinese = Chinese; smaller languages = Other) |
| market_order | Sort order of the market (99 = Other) |
| reviews | Reviews in this language on days 0–90 |
| share_of_reviews | Share of the game's reviews in this language |
| pct_positive | % positive in this language |
| game_pct_positive | The game's overall % positive, all languages |
| gap_points | pct_positive minus game_pct_positive, in points |
| excess_negatives | Negative reviews above what the game's overall negative rate predicts for this many reviews (negative = fewer than expected) |
| reliable | `t` when the language has at least 50 reviews |

### v_market_summary.csv

| Column | Description |
| --- | --- |
| market | Review language market |
| market_order | Sort order of the market |
| core_games_with_50_reviews | Core games with at least 50 reviews in this market (the games included) |
| reviews | Reviews in this market across those games |
| median_gap_points | Median of gap_points across those games |
| games_more_than_5_below | Games where this market is more than 5 points below the game's own score |
| excess_negatives | Sum of excess_negatives across those games |

### v_theme_by_period.csv

| Column | Description |
| --- | --- |
| app_id | Steam app ID |
| game | Game name |
| cohort | `core` or `benchmark` |
| text_group | `English/Polish` or `Chinese`: the keyword set used; the two are never pooled |
| theme | Complaint theme: performance & crashes, bugs, price & value, length & content, story & writing, servers & online, translation |
| theme_order | Sort order of the theme (1–7) |
| period | `week 1` (days 0–6), `days 7-29` or `days 30-90` |
| period_order | Sort order of the period (1–3) |
| negatives_analysed | Negative reviews with text in this text group and period (the denominator) |
| mentions | Negative reviews matching the theme's keywords (one review can match several themes) |
| pct_of_negatives | mentions / negatives_analysed × 100 |
| reliable | `t` when at least 50 negatives were analysed |

### v_first_patch.csv

| Column | Description |
| --- | --- |
| app_id | Steam app ID |
| game | Game name |
| patch_day | Days since launch of the first major patch |
| patch_date | Date of the patch announcement |
| patch_title | Title of the official Steam announcement |
| patch_url | Link to the announcement |
| picks_marked | Patches marked for this game during the manual review (1 = a single clear pick) |

### v_patch_effect.csv

| Column | Description |
| --- | --- |
| app_id | Steam app ID |
| game | Game name |
| patch_day | Days since launch of the first major patch |
| patch_title | Title of the patch announcement |
| days_before | Length of the "before" window (up to 14 days, ending the day before the patch) |
| days_after | Length of the "after" window (up to 14 days, starting the day after the patch; capped at day 90) |
| reviews_before | Reviews in the before window |
| pct_positive_before | % positive in the before window |
| reviews_after | Reviews in the after window |
| pct_positive_after | % positive in the after window |
| change_points | pct_positive_after minus pct_positive_before, in points |
| pct_neg_performance_before | Share of English/Polish negatives mentioning performance & crashes, before |
| pct_neg_performance_after | Same, after |
| pct_neg_bugs_before | Share of English/Polish negatives mentioning bugs, before |
| pct_neg_bugs_after | Same, after |
| reliable | `t` when both windows have at least 50 reviews |
