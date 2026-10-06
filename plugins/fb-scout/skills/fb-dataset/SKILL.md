---
name: fb-dataset
description: Look at, filter and export everything FB Scout has collected. All runs are merged into one dataset with duplicates removed. Gives counts by keyword, kind, language, month posted and group, lists posts and comments with their text, dates and screenshots, exports CSV (Excel), JSONL or Parquet, optionally anonymized, and leaves out items the user marks as false positives or off-topic. Use when the user asks what has been collected, wants numbers or trends, wants to see or read collected posts, or wants an export, CSV or Excel file, or wants posts removed from the dataset.
argument-hint: "[stats | list | label | export] [keyword=...] [kind=...] [language=en,ur,ur-Latn] [sentiment=negative] [since=YYYY-MM-DD] [until=YYYY-MM-DD] [format=csv|jsonl|parquet] [anonymize]"
allowed-tools: mcp__plugin_fb-scout_fb-scout__fb_dataset_stats, mcp__plugin_fb-scout_fb-scout__fb_dataset_items, mcp__plugin_fb-scout_fb-scout__fb_export, mcp__plugin_fb-scout_fb-scout__fb_label_queue, mcp__plugin_fb-scout_fb-scout__fb_label_items, mcp__plugin_fb-scout_fb-scout__fb_exclude_items, mcp__plugin_fb-scout_fb-scout__fb_import_runs, Read
---

# FB Scout dataset

User request: $ARGUMENTS

The dataset (`<output>/fbscout.sqlite`) holds one row per distinct post or comment across all runs.
The same post found again later (even under a different URL) is merged, and `times_seen` counts the
runs that found it. No browser is needed, so these tools are fast and safe to call any time.

## Pick the action

- **Overview** (default, or "what have we collected?"): `fb_dataset_stats` (optionally `keyword`).
  Report the number of distinct items and how many were seen more than once, then counts by keyword,
  kind and language, posts per month (`by_month_posted`) and the top groups. Mention
  `items_without_date` if it is large.
- **Show posts** ("show me the Urdu posts about X from September"): `fb_dataset_items` with filters:
  `keyword`; `kind` / `language` as comma-separated lists; `group`; `since` / `until` (YYYY-MM-DD,
  posted date); `contains`. Show a table: posted date, kind, language, author or group, a short text
  snippet. Page with `offset` when `total` > `returned`. Use `full_text: true` only when the user wants
  to read whole posts. You may `Read` a `screenshot_file` to show or check a post.
- **Export** ("give me a CSV", "for Excel", "for SPSS/R/pandas"): `fb_export`, `format` `csv` by
  default (it opens in Excel with Urdu intact), `jsonl` for scripts, `parquet` if asked (it needs the
  optional pyarrow extra; if the error says so, explain it or offer csv). Same filters as above.
  Report the `file` path and the number of `rows`.
  - `anonymize: true` when the export will be shared, published, or sent to someone outside the
    project. Authors become stable pseudonyms (`author_id`) and all URLs are removed. Always pass on
    the warning: names inside the post text and in screenshots are not removed.

- **Sentiment** ("which posts are negative?", "label the complaints", "only negative ones"): items that
  have no label yet come from `fb_label_queue` (optionally per `keyword`, `run_id` or `batch_id`); read
  them and save labels with `fb_label_items`, following the rubric in that tool's description (same steps
  as `/fb-scout:fb-search` step 4). Then filter `fb_dataset_items` / `fb_export` with
  `sentiment: "negative"`. `fb_dataset_stats` shows `by_sentiment` and `not_labeled`. Labels are per item
  and keyword (a post can be negative about one brand and positive about another).
- **Marketplace listings**: `kind: "marketplace"`; they have `price`, `location` and, when details were
  opened, `condition` and the seller as author.
- **Remove items** ("this one is not about the brand", "drop the false positives"): show the user the
  items first (`fb_dataset_items`) and remove only the ones they confirm, with `fb_exclude_items`
  (`item_ids`, a short `reason`). They stay out of later imports; the run folders are not changed.
  To undo, the entry is deleted from `exclusions.json` and `fb_import_runs` is run.

## Language codes

`en` English, `ur` Urdu (Urdu script), `ur-Latn` Roman Urdu, `tl` Tagalog, `ar`, `fa`, `hi`, `bn`,
`und` = undetermined (e.g. only hashtags).

Sentiment labels: `negative`, `neutral`, `positive` (set by Claude, or by hand with `fbscout db label`).

## Problems

- `no_dataset`: nothing has been collected yet. Suggest `/fb-scout:fb-search`.
- Runs that are missing (made before v0.2, or copied in from another computer): `fb_import_runs`.
  It is safe to repeat.

Report only what the tools return. Never invent counts, posts or URLs.
