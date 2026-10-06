---
name: fb-batch
description: Run a Facebook research study with FB Scout. Searches several keywords, both in global search and inside a fixed list of Facebook groups, one search at a time, and adds every run to the dataset. Use when the user wants to search more than one keyword or more than one group, monitor a list of groups, run or repeat a study file, or schedule regular collection.
argument-hint: "<study.json> | keywords=<k1,k2> groups=<url1,url2> [max=20] [no-global] [comments] [blur]"
allowed-tools: mcp__plugin_fb-scout_fb-scout__fb_status, mcp__plugin_fb-scout_fb-scout__fb_login, mcp__plugin_fb-scout_fb-scout__fb_batch, mcp__plugin_fb-scout_fb-scout__fb_dataset_stats, Read
---

# Facebook research study (FB Scout batch)

User request: $ARGUMENTS

## 1. Work out the study

Either:
- **A study file**: a path to a `.json` file → `study_file`. `Read` it if the user asks what's in it.
- **Inline**: `keywords` (list), `group_urls` (list of Facebook group URLs), `include_global` (false if
  the user says `no-global` or "only in the groups"), `max_results` (`max=N`, default 20),
  `include_comments`, `blur_names`.

If there are no keywords, ask for them and stop.

## 2. Check the login

Call `fb_status`. If `logged_in` is false, follow the login steps of `/fb-scout:fb-search` (step 2), then continue.

## 3. Show the plan first

Call `fb_batch` with the same parameters and `dry_run: true`. Show the user the list of searches and
`estimated_minutes`. Ask for confirmation before running if there are more than 6 searches or the
estimate is over 30 minutes. Otherwise say what will run and continue.

Keep studies small. Each search has a 1–3 minute pause before it to protect the account. More than
about 12 searches in one go should be split up or run from the terminal (see Scheduling).

## 4. Run it

Call `fb_batch` once without `dry_run`. It runs in a hidden browser, one search at a time.

- Never start a second `fb_batch` or `fb_search` while one is running.
- If the result has `status: "stopped"`, read `stopped_reason`. For `checkpoint` or `blocked`, tell the
  user to resolve it by hand and **wait** (hours for `blocked`). Do not retry. For `not_logged_in`, log in
  again and offer to re-run only the searches that did not run.

## 5. Report

- A table with one row per search: keyword, global search or group, status, `stats.saved`, how many
  were new in the dataset (`dataset.new_items`) and how many were already known (`dataset.merged`).
- Any errors, in plain words.
- `dataset_totals`: distinct items in the dataset and per keyword.
- `report_file`: the batch report (JSON).
- Offer `/fb-scout:fb-dataset` to look at the results or export them.

## Scheduling

A study can be repeated (e.g. daily) with the operating system's scheduler, which runs the CLI on this
computer. The browser login lives on this computer, so cloud schedulers can't do it. Give the user
the right command for their system:

- Windows (Task Scheduler), daily at 10:00:
  `schtasks /Create /SC DAILY /ST 10:00 /TN "FB Scout study" /TR "uv run --directory \"<plugin>\server\" fbscout batch \"<study.json>\""`
- macOS/Linux (cron), daily at 10:00:
  `0 10 * * * uv run --directory "<plugin>/server" fbscout batch "<study.json>" >> ~/fbscout-batch.log 2>&1`

Set `FBSCOUT_OUTPUT_DIR` in the scheduled environment so results go to the same folder as before.
Recommend once a day at most.
