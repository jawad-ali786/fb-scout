# FB Scout — Dataset (Phase 2)

Every run still writes its own `results.json` and screenshots (see [MVP.md](MVP.md#4-output)).
From v0.2, each run is also added to one **SQLite dataset** per output folder, where the
same post found by several runs becomes a single item:

```
fb-scout-output/
├── fbscout.sqlite          ← the dataset (all runs, duplicates merged)
├── exclusions.json         ← records left out of the dataset, with reasons
├── labels.json             ← sentiment labels (negative / neutral / positive), see SENTIMENT.md
├── _exports/               ← CSV / JSONL / Parquet exports
├── _batches/               ← one report per study (batch) run
└── <keyword>/<timestamp>/  ← the runs, as before
```

The dataset is updated automatically after every search. `fbscout db import` (MCP:
`fb_import_runs`) imports runs made before v0.2 or copied in from another computer. Importing
is idempotent: importing a run again adds nothing. The path can be changed with `FBSCOUT_DB`.

---

## 1. Tables

| Table | One row per | Main columns |
|---|---|---|
| `runs` | run | `run_id`, `keyword`, `scope`, `group_url`, `status`, `started_at`, `run_dir`, `batch_id`, `stats` |
| `items` | distinct post or comment | see below |
| `sightings` | time a run found an item | `item_id`, `run_id`, `keyword`, `search_rank`, `matched_in`, `match_snippet`, `screenshot_path`, `captured_at` |

`items` columns:

| Column | Meaning |
|---|---|
| `item_id` | stable id of the post/comment in the dataset (`i_…`) |
| `kind` | `post`, `group_post`, `comment`, `reply`, `reel`, `video`, `photo`, `event`, `marketplace` (a listing), `profile` (a person/page card, only with `include_name_matches`), `unknown` (the most specific kind any run saw) |
| `post_url`, `comment_url`, `parent_post_url` | cleaned URLs |
| `author_name`, `author_url`, `group_name`, `group_url` | |
| `text` | the longest text any run captured (e.g. with "See more" expanded) |
| `image_text` | Facebook's own description of the post's images, e.g. *"May be an image of text that says 'SOLAR PANEL SALE'"* |
| `time_text`, `time_exact` | raw time as shown by Facebook (label / hover tooltip) |
| `posted_at`, `posted_date` | parsed time, ISO 8601 with the browser's UTC offset (see §3) |
| `posted_at_precision` | `minute`, `hour`, `day`, `week`, `month` or `year` |
| `posted_at_source` | `time_exact` (tooltip) or `time_text` (relative label) |
| `language` | see §4 |
| `price`, `location`, `condition` | Marketplace listings: price as shown (`PKR8,000`, `FREE`), place, condition (`Used – good`, with `listing_details`) |
| `content_type`, `content_reason` | `promotion`, `job`, `giveaway`, `spam` or `marketplace`, and why (empty for ordinary posts); these are hidden unless asked for, see [Content filter](#content-filter) |
| `screenshot_path` | first screenshot, relative to the output folder |
| `first_seen`, `last_seen`, `times_seen` | when runs found it, and in how many runs |

A fourth table, `labels`, holds the sentiment labels per item and keyword (see [SENTIMENT.md](SENTIMENT.md)).

To use the tables directly: `sqlite3 fb-scout-output/fbscout.sqlite`, DB Browser for SQLite,
`pandas.read_sql`, or R's `DBI`.

---

## 2. How duplicates are found

Facebook gives the same post different URLs, so URL equality is not enough:

- `pfbid…` post ids (`/permalink.php?story_fbid=pfbid…`, `/<page>/posts/pfbid…`) are
  **different in every session**.
- A multi-photo post is reached through a different photo each time (`/photo/?fbid=…`).
- Group posts appear as `/groups/<g>/posts/<id>/` or `/groups/<g>/?multi_permalinks=<id>`.

So a record is matched to an existing item in this order:

1. **Stable URL key**: the cleaned URL when it has numeric ids (group posts, reels, videos,
   numeric `story_fbid`), or `comment:<id>` for comments and replies (the comment id is stable
   whatever form the post URL has). `pfbid` and `/share/` links are not used.
2. **Content key**: the group (for comments: the parent post) + a fingerprint of the text
   (letters and digits of the first 160 characters, any script, lower case). This applies
   when at least one side has no stable URL (or only a photo URL) **and** the authors don't
   contradict each other (same name, or one unknown).

Two posts with identical text in **different groups** stay separate (a cross-posted ad is
counted once per group). The same text by **different authors** also stays separate. Texts
shorter than 12 letters ("Interested", "Price?") are never matched by content.

Import also fixes what older runs stored differently: tracking parameters, the old
`multi_permalinks` form, photo links of group posts (turned into the group post URL from
`set=gm.<id>` / `set=pcb.<id>`), avatar labels stored as author names, and image
descriptions stored as `time_text`.

---

### Removing items (exclusions)

False positives or posts that are off-topic can be left out of the dataset:

```
fbscout db exclude i_1a2b3c4d5e6f i_6f5e4d3c2b1a --reason "not about the brand"
```
(MCP: `fb_exclude_items`). Each excluded record is written with its run, record id,
keyword, reason and date to `exclusions.json` next to the dataset, and every later import
skips it, also when the dataset is rebuilt from the run folders. The run folders are never
changed, so the raw evidence stays complete. To restore a record, delete its entry from
`exclusions.json` and run `fbscout db import`. `fbscout db stats` shows `excluded_records`.

The 15 false positives from the first Phase 1 test (member cards and keyword-only-in-name
matches, see PLAN.md §9) were removed this way.

### Content filter

Posts that aren't people talking about the keyword are tagged with a `content_type`, and the
reason is saved in `content_reason`:

| `content_type` | What |
|---|---|
| `promotion` | ads, items or services for sale, price lists, stock offers, the brand page's own posts and announcements |
| `job` | hiring posts, vacancies, "technician required" |
| `giveaway` | contests, lucky draws, "tag 3 friends" |
| `marketplace` | every Marketplace listing (an item for sale); a Marketplace search keeps them, the dataset hides them |
| `spam` | earn-money, forex / crypto signals, loan offers, follow-for-follow |

Searches leave them out (see [MVP.md](MVP.md#changes-in-v032); a Marketplace search keeps its
listings), and the dataset hides them from `stats`, `items`, the label queue and exports unless
`include_types` names them (`all` shows everything). Filtering on `kind: marketplace` shows listings
too. `stats` reports what it hides in `hidden_by_content_type`. Nothing is deleted: the
items stay in the dataset and in the run folders.

How it decides
([`content_filter.py`](../plugins/fb-scout/server/src/fbscout/content_filter.py)): rules in
English, Roman Urdu and Urdu give points, for example "for sale" 3, a phone or WhatsApp number 2,
"6,050 each" 2, "in stock" 2, a specification sheet 2, advertising copy ("upgrade your",
"engineered for") 1 per phrase up to 3, and a page whose name contains the keyword (the brand's
own page) 3. Three points make a type. Listings are always `marketplace`. Someone describing their own experience or asking a
question ("worst service", "kharab", "my inverter", "I bought", "is it normal", "anyone using")
takes 4 points off, so a complaint that mentions a price or a phone number is kept. Profile
cards are not checked.

Every item is checked when it is imported, and the whole dataset is checked again when the rules
change (the dataset stores the rules' version in `meta`), so older runs are covered too.

On the 134 items collected for the first tests (mostly searches for brand names), it hid 114: 112
promotions, 1 job post and 1 giveaway. It kept all 5 posts labelled negative and every question and
experience. (The 5 Marketplace listings are hidden too since v0.3.3.) Rules like these miss some ads in other languages
(e.g. Burmese) and will sometimes hide a real post. Check `filtered_examples` in a run, or the
hidden items with `include_types`, and extend the cue lists when something is in the wrong place.

---

## 3. Dates

| Source | Examples | Precision |
|---|---|---|
| Hover tooltip (`time_exact`) | `Monday 10 August 2026 at 14:15`, `Sunday, September 28, 2026 at 4:12 PM` | minute |
| Label (`time_text`), relative to `captured_at` | `12m`, `5h`, `3d`, `2w`, `1y`, `Yesterday at 10:00`, `Saturday at 9:30 PM`, `September 28 at 4:12 PM`, `March 3, 2024` | as written |

Facebook shows times in the **browser's timezone**. Each run records it
(`browser_timezone`, `browser_utc_offset_minutes` in `results.json`), and `posted_at`
carries that offset (`2026-09-28T16:12:00+05:00`). Runs made before v0.2 don't have it, so
the importing computer's current offset is assumed.

Relative labels are approximate: `3d` means "about 3 days before capture". Filter on
`posted_at_precision` when exact dates matter. Facebook search mostly returns recent
posts, so expect a recency bias.

---

## 4. Language

Rule-based and reproducible (no model, no download): the majority script decides first,
then common words for Latin-script text.

| Code | Language | Rule |
|---|---|---|
| `ur` | Urdu (Urdu script) | Arabic script with Urdu-only letters (ٹ ڈ ڑ ں ے) |
| `ar` / `fa` | Arabic / Persian | Arabic script without them |
| `hi` / `bn` | Hindi / Bengali | Devanagari / Bengali script |
| `ur-Latn` | Roman Urdu | 2+ Roman Urdu words (`hai`, `ka`, `ki`, `aur`, `nahi`, …) |
| `tl` | Tagalog / Filipino | 2+ Tagalog words (`mga`, `ang`, `ng`, `po`, …) |
| `en` | English | English words, and fewer than 2 Roman Urdu/Tagalog words |
| `und` | undetermined | too short, or only hashtags / numbers / product codes |

Code-mixed posts (English product words with Urdu grammar) count as `ur-Latn`, which is
what most Pakistani "English" posts are. Word lists are in `src/fbscout/lang.py`. When the
rules change, `fbscout db import` updates the stored labels.

---

## 5. Text inside images

Facebook runs its own OCR and puts the result in the image's description ("alt text"),
e.g. *May be an image of text that says 'SOLAR PANEL CLEANING 50% OFF'*. FB Scout keeps
it as `image_text`, and the keyword check also looks there: such records get
`matched_in: "image_text"`. Coverage depends on Facebook. Sometimes it only says *"May be
an image of text"*, without the text. Running a local OCR engine on the screenshots was
left out on purpose: it would add a large download for every user, and the screenshots
can be read later (e.g. by the Phase 3 LLM step) when needed.

---

## 6. Exports

`fbscout db export` / `fb_export`: one row per item with the columns of §1 plus
`keywords` (all keywords that found the item) and `first_run_id`.

| Format | Use |
|---|---|
| `csv` (default) | Excel (UTF-8 with BOM, so Urdu and emoji display correctly), SPSS, R |
| `jsonl` | scripts, LLM labelling |
| `parquet` | pandas/Arrow; needs `uv sync --extra parquet` in `plugins/fb-scout/server` |

Filters: `keyword`, `kind`, `language`, `sentiment`, `run_id`, `batch_id`, `group`,
`since`/`until` (posted date), `contains` (text, image text, price or location), `include_types`
(also export promotions, job posts, giveaways, spam or Marketplace listings; `all` = everything). Columns include
`sentiment`, `sentiment_reason`, `price`, `location`, `condition`, `content_type` and `content_reason`.

**Anonymized export** (`--anonymize` / `anonymize: true`): `author_name` and `author_url`
are replaced by `author_id`, a pseudonym that stays the same across exports
(HMAC-SHA256 with a random secret salt stored in `~/.fbscout/anon_salt`, outside the
data folder, so pseudonyms can't be reversed by hashing a list of names). All URLs are
dropped, because profile and permalink URLs contain user ids. **Not removed:** names
written inside the post text, and names in screenshots. For screenshots, collect with
`blur_names` (CLI `--blur-names`), which blurs links to people's and pages' profiles (names
and profile pictures) in the screenshot. Faces in photos are not blurred.

---

## 7. Studies (batches)

A study file lists keywords and groups. `fbscout batch study.json` (MCP: `fb_batch`)
runs every keyword in global search and inside every group, one search at a time, with a
random 1–3 minute pause in between (`pause_seconds`). It stops at the first
checkpoint/block/login problem. Every run gets the study's `batch_id`, and a report goes
to `_batches/<batch_id>.json`. See [`examples/study.example.json`](../examples/study.example.json).

| Setting | Default | |
|---|---|---|
| `keywords` | (required) | list |
| `groups` | `[]` | Facebook group URLs |
| `include_global` | `true` | also search all of Facebook |
| `max_results` | 20 | per search |
| `match_mode` | `phrase` | `phrase` / `all` / `any` |
| `include_comments`, `max_comment_posts` | `false`, 5 | |
| `max_minutes_per_search` | 10 | |
| `pause_seconds` | `[60, 180]` | random pause between searches (× `FBSCOUT_PACE`) |
| `blur_names` | `false` | |
| `include_marketplace` | `false` | also one Marketplace search per keyword |
| `marketplace_location` | near the account | Marketplace city (`karachi`) or location id |
| `listing_details` | `false` | open each kept listing (description, seller, condition, date) |
| `include_name_matches` | `false` | keep keyword-only-in-a-name posts and profile cards |
| `include_types` | `[]` | also keep `promotion`, `job`, `giveaway`, `spam` (or `"all"`), see [Content filter](#content-filter); listings from `include_marketplace` are always kept |
| `output_dir` | default output folder | |

At most 50 searches per batch. For repeated collection, schedule the CLI with Task
Scheduler or cron (see the README), at most once a day.
