# FB Scout

A Claude Code plugin (and plain MCP server + CLI) for university research.
**Give it a keyword → it searches Facebook, keeps only posts and comments that
really contain the keyword, screenshots each one with the keyword highlighted,
and saves a `results.json` with metadata** (post URL, kind, author, group,
time, text, screenshot name).

It also searches **Marketplace** listings, and can return **only negative posts**:
Claude reads every result and labels it negative / neutral / positive with a reason
(see [docs/SENTIMENT.md](docs/SENTIMENT.md)).

Every run also goes into one **SQLite dataset** where the same post found by
several runs is merged. It adds parsed dates, language detection (English / Urdu /
Roman Urdu / ...) and the text Facebook read from images. A study file runs
keywords across a fixed list of groups, and the dataset exports to CSV, JSONL or
Parquet, optionally anonymized.

Later phases add sentiment, churn and product-feedback analysis on this data.
See [docs/PLAN.md](docs/PLAN.md) for the full plan, [docs/MVP.md](docs/MVP.md)
for the search itself and [docs/DATASET.md](docs/DATASET.md) for the dataset.

---

## Repository layout

```
.claude-plugin/marketplace.json     ← makes this repo installable with /plugin
plugins/fb-scout/
  .claude-plugin/plugin.json        ← plugin manifest
  .mcp.json                         ← starts the MCP server with uv
  skills/fb-search/SKILL.md         ← /fb-scout:fb-search, one keyword
  skills/fb-batch/SKILL.md          ← /fb-scout:fb-batch, a study: keywords × groups
  skills/fb-dataset/SKILL.md        ← /fb-scout:fb-dataset, stats, browsing, exports
  server/                           ← Python package "fbscout" (MCP server + CLI + tests)
    src/fbscout/
      browser.py   Chrome with a saved profile, login, checkpoint detection
      scraper.py   search → scroll → extract → verify → screenshot pipeline
      extract.py   ALL Facebook page knowledge (fix this file when Facebook changes)
      urls.py      URL cleaning + kind classification
      matching.py  keyword verification
      capture.py   element screenshots + keyword highlight + name blurring
      storage.py   run folders + results.json
      dataset.py   SQLite dataset across runs, deduplication
      dates.py     "3d" / tooltip dates → posted_at
      lang.py      language detection (en / ur / ur-Latn / ...)
      batch.py     study files: keywords × groups, one search at a time
      export.py    CSV / JSONL / Parquet, anonymized exports
      mcp_server.py / cli.py / api.py
    tests/         unit tests + offline browser tests on fake Facebook pages
docs/PLAN.md, docs/MVP.md, docs/DATASET.md, docs/SENTIMENT.md
examples/study.example.json         ← a study file to copy
```

---

## Prerequisites (each machine, one time)

1. **Claude Code**
2. **uv**, which installs Python and all dependencies automatically:
   - Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
   - macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
3. **Google Chrome**. Microsoft Edge also works; FB Scout falls back to it automatically.

---

## Install

**Option A: from GitHub (recommended).** The code is at https://github.com/jawad-ali786/fb-scout. In Claude Code run:
```
/plugin marketplace add jawad-ali786/fb-scout
/plugin install fb-scout@fb-scout-marketplace
```

**Option B: from a local folder or a copy of this repo:**
```
/plugin marketplace add "E:\path\to\this\repo"
/plugin install fb-scout@fb-scout-marketplace
```

**Option C: try it without installing (development):**
```
claude --plugin-dir "./plugins/fb-scout"
```

> The **first** start downloads the Python dependencies (about 1 minute). If
> `/mcp` shows `fb-scout` as failed right after installing, wait a moment and
> reconnect it from `/mcp`.

---

## Use

In Claude Code:
```
/fb-scout:fb-search "solar panel" max=15
/fb-scout:fb-search "Brand X" group=https://www.facebook.com/groups/123456 comments
/fb-scout:fb-search "Brand X problem" match=all negative
/fb-scout:fb-search "solar panel" marketplace city=karachi details
/fb-scout:fb-batch examples/study.example.json
/fb-scout:fb-dataset stats
/fb-scout:fb-dataset export language=ur,ur-Latn anonymize
```
Or just ask: *"search facebook for 'Brand X' and save 20 posts"*, *"run 'Brand X'
and 'Brand Y' in these three groups"*, *"how many Roman Urdu posts did we collect in
September?"*, *"give me a CSV for Excel"*, *"show me only the complaints about Brand X"*,
*"find solar panels for sale on Marketplace in Lahore"*.

By default a post only counts when the keyword is in the post itself. Results where it is
only in a person's, page's or group's name (and profile / group-member cards) are left out;
switch them on with `names` / `include_name_matches` / `--name-matches`.

**First run:** the agent sees you're not logged in and logs you in once.
Pick one of three ways:

| Way | What happens |
|---|---|
| **Normal Chrome window** (default) | Your real Chrome opens as a normal window: not automated, no "controlled by automated software" bar. Log in with a **dedicated research account**, wait for your feed, then **close the window**. |
| **Copy from Firefox** | If you're already logged in to Facebook in Firefox, FB Scout copies that login (facebook.com cookies only). Say "log in from Firefox" or run `fbscout login --from-firefox`. |
| **Cookie file** | Export cookies from any browser with an extension (e.g. "Get cookies.txt LOCALLY"), then run `fbscout login --cookies file.txt`. Delete the file afterwards; it works like a password. |

The login is saved in `~/.fbscout/`, outside the code folder and separate
from your normal Chrome profile. You only do this once.

> Chrome and Edge profiles can't be copied directly, because Chrome encrypts
> saved logins so that only Chrome can read them. Use the window or a cookie
> file for those. For the same reason, searches run in Chrome's own headless
> mode, not a separate Chromium.

**Every search after that runs hidden (headless)**, with no window. Add
`show_browser` (MCP) or `--show-browser` (CLI) to watch a run.

> Why a login at all? Facebook's keyword search returns "Not Found" when
> logged out. Logged-out public Pages show only 3–5 posts before a login
> wall blocks further scrolling.

Results:
```
fb-scout-output/<keyword>/<timestamp>/results.json
                                     /screenshots/001_post_3f2a9c1b.png ...
fb-scout-output/fbscout.sqlite        ← the dataset: all runs, duplicates merged
fb-scout-output/exclusions.json       ← records left out of the dataset, with reasons
fb-scout-output/labels.json           ← sentiment labels, restored on rebuild
fb-scout-output/_exports/             ← CSV / JSONL / Parquet exports
fb-scout-output/_batches/             ← study (batch) reports
```
The `results.json` format is documented in [docs/MVP.md](docs/MVP.md#4-output),
the dataset, dates, languages and exports in [docs/DATASET.md](docs/DATASET.md).

### Studies: several keywords across a list of groups

Copy [`examples/study.example.json`](examples/study.example.json), put your keywords
and group URLs in it, and run it with `/fb-scout:fb-batch my-study.json` or
`fbscout batch my-study.json` (add `--dry-run` to see the plan first). Searches run one
at a time with a 1–3 minute pause in between, and the batch stops at the first
checkpoint or block.

**Run it on a schedule** (the login lives on this computer, so use the computer's own
scheduler, at most once a day):
```
# Windows (Task Scheduler), daily at 10:00
schtasks /Create /SC DAILY /ST 10:00 /TN "FB Scout study" /TR "uv run --directory \"E:\path\to\plugins\fb-scout\server\" fbscout batch \"E:\path\to\my-study.json\""

# macOS / Linux (crontab -e), daily at 10:00
0 10 * * * uv run --directory "/path/to/plugins/fb-scout/server" fbscout batch "/path/to/my-study.json" >> ~/fbscout-batch.log 2>&1
```
Set `FBSCOUT_OUTPUT_DIR` for the scheduled task so results land in the same folder.

---

## Use without Claude Code

**CLI** (run inside `plugins/fb-scout/server`):
```
uv run fbscout login                      # normal Chrome window, close it after logging in
uv run fbscout login --from-firefox       # or: copy the login from Firefox
uv run fbscout login --cookies file.txt   # or: import an exported cookie file
uv run fbscout search "solar panel" --max 20 [--group URL] [--match all] [--comments] [--name-matches] [--blur-names] [--show-browser]
uv run fbscout search "solar panel" --marketplace [--location karachi] [--listing-details]
uv run fbscout batch study.json [--dry-run]
uv run fbscout runs
uv run fbscout db stats [--keyword K]
uv run fbscout db export [--format csv|jsonl|parquet] [--anonymize] [--keyword K] [--language ur,ur-Latn] [--since 2026-09-01]
uv run fbscout db exclude i_... --reason "off-topic"   # leave items out (run folders stay unchanged)
uv run fbscout db label i_... --sentiment negative --reason "..."   # label by hand (e.g. a gold set)
uv run fbscout db export --sentiment negative --keyword "Brand X"    # only the negatives
uv run fbscout db import                  # runs made before v0.2, or copied from elsewhere
```

**Any other MCP client** (Claude Desktop, Cursor, other vendors' agents): add this server:
```json
{
  "mcpServers": {
    "fb-scout": {
      "command": "uv",
      "args": ["run", "--quiet", "--no-dev", "--directory", "/absolute/path/to/plugins/fb-scout/server", "fbscout-mcp"],
      "env": { "FBSCOUT_OUTPUT_DIR": "/absolute/path/for/results" }
    }
  }
}
```

### MCP tools
| Tool | Purpose |
|---|---|
| `fb_status` | Logged in? Which browser? Where do results go? |
| `fb_login` | `method`: `browser` (normal Chrome window, close it when logged in), `firefox` (copy login), `cookie_file` (+ `cookie_file` path); `force` to switch account |
| `fb_search` | `keyword`, `max_results`, `source` (`posts`/`marketplace`), `group_url`, `match_mode` (`phrase`/`all`/`any`), `include_comments`, `max_comment_posts`, `include_name_matches`, `marketplace_location`, `listing_details`, `only_negative`, `output_dir`, `save_unverified`, `highlight`, `max_minutes`, `show_browser`, `blur_names` |
| `fb_batch` | A study: `study_file`, or `keywords` + `group_urls` (+ `include_marketplace` and the search options); `dry_run` shows the plan |
| `fb_list_runs` | Previous runs with stats |
| `fb_dataset_stats` | Distinct items by keyword, kind, language, month posted, group |
| `fb_dataset_items` | Items with filters (`keyword`, `kind`, `language`, `sentiment`, `run_id`, `batch_id`, `group`, `since`, `until`, `contains`), paged |
| `fb_label_queue` | Items that still need a sentiment label, with their text |
| `fb_label_items` | Save labels: negative / neutral / positive + reason (rubric in the tool description) |
| `fb_export` | CSV / JSONL / Parquet with the same filters; `anonymize` |
| `fb_exclude_items` | Leave items out of the dataset with a reason (kept in `exclusions.json`) |
| `fb_import_runs` | Import existing run folders into the dataset (safe to repeat) |

---

## Settings (environment variables, all optional)

| Variable | Default | Meaning |
|---|---|---|
| `FBSCOUT_OUTPUT_DIR` | `<project>/fb-scout-output` | Where runs are saved |
| `FBSCOUT_DB` | `<output dir>/fbscout.sqlite` | The dataset file |
| `FBSCOUT_HOME` | `~/.fbscout` | Browser profile (your login) and the anonymization salt |
| `FBSCOUT_BROWSER` | try `chrome`, `msedge`, `chromium` | Force one browser |
| `FBSCOUT_PACE` | `1` | Delay multiplier (higher = slower, safer) |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `not_logged_in` | Run `/fb-scout:fb-search` again, which triggers the login. Or run `uv run fbscout login`. With the window method, close the window only **after** your feed shows. |
| `no_facebook_login` | The Firefox profile or cookie file has no logged-in Facebook session. Log in there first. |
| `checkpoint` / `blocked` | Resolve it by hand in the browser (`fbscout login`), then **wait** (hours for "blocked"). Don't retry in a loop. |
| `profile_in_use` | Another FB Scout Chrome window is open. Close it. |
| `browser_unavailable` | Install Chrome, or run `uv run playwright install chromium` in `plugins/fb-scout/server`. |
| Hidden run finds 0 posts but login is OK | Retry with `--show-browser`. Facebook may treat headless differently. |
| 0 results / missing fields | Facebook changed its page. Look in the run's `debug/` folder and update `extract.py`. Set the account's Facebook language to **English**. |

---

## Development

```
cd plugins/fb-scout/server
uv run pytest            # unit tests + offline browser tests (uses installed Chrome, headless)
claude plugin validate ../   # check the plugin manifest
```

---

## Responsible use

Automated collection is against Facebook's Terms, and posts contain personal
data. Use a dedicated research account and small volumes, and get your
university's ethics approval. Never publish raw screenshots with names. See
the checklist in [docs/PLAN.md](docs/PLAN.md#8-research-ethics-checklist-for-the-university).
For large-scale studies, consider applying for Meta's official **Meta Content
Library**, which is built for researchers.
