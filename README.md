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
AGENTS.md                           ← instructions for Cursor, Codex, GitHub Copilot (setup, rules)
.agents/skills/                     ← the three skills for Cursor, Codex and Copilot (generated)
.cursor/mcp.json                    ← starts the MCP server in Cursor
.codex/config.toml                  ← … in OpenAI Codex
.vscode/mcp.json                    ← … in GitHub Copilot (VS Code)
.github/mcp.json                    ← … in GitHub Copilot CLI
scripts/sync_agent_skills.py        ← writes .agents/skills from the Claude Code skills
plugins/fb-scout/
  .claude-plugin/plugin.json        ← plugin manifest
  .mcp.json                         ← starts the MCP server with uv
  hooks/hooks.json                  ← at session start: a quick check, and the setup in the background
  scripts/session-start.sh          ← installs uv, Python, packages (and a browser if needed) once
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
      content_filter.py  promotions / job posts / giveaways / spam / listings (left out unless asked)
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

1. **Claude Code**, or Cursor, OpenAI Codex or GitHub Copilot (see
   [Use with Cursor, Codex or GitHub Copilot](#use-with-cursor-codex-or-github-copilot))
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

### First time on a new computer

1. **Add the plugin** (the two `/plugin` commands above) and **start a new Claude Code session**.
   Nothing else to install by hand: FB Scout sets itself up **in the background** while you chat.
   It installs [`uv`](https://docs.astral.sh/uv/) if it's missing (with uv's official installer),
   then Python and the packages (about 100 MB, 1–2 minutes, only once), and Playwright's Chromium
   if neither Google Chrome nor Microsoft Edge is installed. A note says that setup is running.
   Claude doesn't wait for it, and tells you when it's done.
2. **When Claude says FB Scout is ready**, type `/mcp reconnect all` once, because Claude Code
   doesn't retry a tool server that couldn't start. Only if `uv` was just installed and this
   window can't see it yet does Claude ask you to restart Claude Code instead.
3. **Search**: `/fb-scout:fb-search "solar panel" max=20`. The very first time, a Chrome window
   opens for the one-time Facebook login. Log in, close the window, and the search continues by
   itself in the same prompt.

From then on, sessions start without any setup (the check takes about 0.2 s), and plugin updates
re-install in a few seconds from the cache. The setup log is in the plugin's data folder
(`~/.claude/plugins/data/<plugin id>/setup.log`). To install `uv` yourself instead, set
`FBSCOUT_NO_AUTO_INSTALL=1`; Claude then gives you the command:
- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

**How long a search takes:** about 5 seconds per saved post, so 20 posts take 1–2 minutes;
`comments` adds roughly half a minute per scanned post. About half of that time is deliberate
human-like pausing while scrolling, which keeps the research account from being flagged. A
lower `FBSCOUT_PACE` (e.g. `0.5`) is faster but riskier.

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

**Ads, job posts, giveaways and spam are left out too**, because they aren't people talking about
the keyword: items or services for sale, price lists, the brand page's own posts and announcements
(`promotion`), hiring posts (`job`), "tag 3 friends" contests (`giveaway`), earn-money and
forex offers (`spam`). Someone describing their own experience or asking a question is kept even
when the post mentions a price or a phone number. Each search reports how many were left out
(`filtered_out`), and `results.json` lists the first ones with the reason, so the filter can be
checked. To keep any of them, say so (*"include ads"*, *"job posts too"*) or pass
`include_types` / `--include promotion,job` (`all` keeps everything). The dataset hides them the
same way unless asked for. **Marketplace listings** are items for sale too: a Marketplace search keeps
them (you asked for them), but the dataset hides them from counts, lists, labeling and exports unless
you ask for them (`include_types: marketplace`, `--include marketplace`, or `kind=marketplace`). The
rules are in
[`content_filter.py`](plugins/fb-scout/server/src/fbscout/content_filter.py) and described in
[docs/DATASET.md](docs/DATASET.md#content-filter).

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

## Use with Cursor, Codex or GitHub Copilot

Clone this repository and open its folder in the agent. It finds everything there:

| Agent | MCP server | Skills | Instructions |
|---|---|---|---|
| Cursor (editor and CLI) | `.cursor/mcp.json` | `.agents/skills/` | `AGENTS.md` |
| OpenAI Codex (CLI, IDE extension, app) | `.codex/config.toml` | `.agents/skills/` | `AGENTS.md` |
| GitHub Copilot in VS Code (Agent mode) | `.vscode/mcp.json` | `.agents/skills/` | `AGENTS.md` |
| GitHub Copilot CLI | `.github/mcp.json` | `.agents/skills/` | `AGENTS.md` |

There's no automatic background setup like in Claude Code. Once per computer, run in the repository root:
```
uv sync --inexact --frozen --no-dev --project plugins/fb-scout/server
```
(plus `uv run --project plugins/fb-scout/server playwright install chromium` if neither Chrome nor Edge
is installed). Then switch on the project's `fb-scout` server: Cursor and VS Code ask for approval. Codex
and Copilot CLI read the project config only in a **trusted** folder, and must be **started in the
repository root**, because their configs can't name the folder and use a relative path. Details are in
[AGENTS.md](AGENTS.md).

The skills are called by name: `/fb-search "solar panel" max=15` in Cursor and Copilot, `$fb-search` in
Codex, and the same for `fb-batch` and `fb-dataset`. Or just ask, as in Claude Code. Results go to
`fb-scout-output/` in the repository folder.

The Copilot coding agent (the one that runs on GitHub) can't use FB Scout: it needs the browser login on
your computer.

---

## CLI and other MCP clients

**CLI** (run inside `plugins/fb-scout/server`):
```
uv run fbscout login                      # normal Chrome window, close it after logging in
uv run fbscout login --from-firefox       # or: copy the login from Firefox
uv run fbscout login --cookies file.txt   # or: import an exported cookie file
uv run fbscout search "solar panel" --max 20 [--group URL] [--match all] [--comments] [--name-matches] [--include promotion,job|all] [--blur-names] [--show-browser]
uv run fbscout search "solar panel" --marketplace [--location karachi] [--listing-details]
uv run fbscout batch study.json [--dry-run]
uv run fbscout runs
uv run fbscout db stats [--keyword K] [--include all]
uv run fbscout db export [--format csv|jsonl|parquet] [--anonymize] [--keyword K] [--language ur,ur-Latn] [--since 2026-09-01] [--include promotion]
uv run fbscout db exclude i_... --reason "off-topic"   # leave items out (run folders stay unchanged)
uv run fbscout db label i_... --sentiment negative --reason "..."   # label by hand (e.g. a gold set)
uv run fbscout db export --sentiment negative --keyword "Brand X"    # only the negatives
uv run fbscout db import                  # runs made before v0.2, or copied from elsewhere
```

**Any other MCP client** (Claude Desktop, other vendors' agents): add this server:
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
| `fb_search` | `keyword`, `max_results`, `source` (`posts`/`marketplace`), `group_url`, `match_mode` (`phrase`/`all`/`any`), `include_comments`, `max_comment_posts`, `include_name_matches`, `marketplace_location`, `listing_details`, `only_negative`, `include_types`, `output_dir`, `save_unverified`, `highlight`, `max_minutes`, `show_browser`, `blur_names` |
| `fb_batch` | A study: `study_file`, or `keywords` + `group_urls` (+ `include_marketplace` and the search options); `dry_run` shows the plan |
| `fb_list_runs` | Previous runs with stats |
| `fb_dataset_stats` | Distinct items by keyword, kind, language, month posted, group; how many are hidden as ads / jobs / ... (`include_types` to count them) |
| `fb_dataset_items` | Items with filters (`keyword`, `kind`, `language`, `sentiment`, `run_id`, `batch_id`, `group`, `since`, `until`, `contains`, `include_types`), paged |
| `fb_label_queue` | Items that still need a sentiment label, with their text (ads etc. only with `include_types`) |
| `fb_label_items` | Save labels: negative / neutral / positive + reason (rubric in the tool description) |
| `fb_export` | CSV / JSONL / Parquet with the same filters (incl. `include_types`); `anonymize` |
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

The skills are written for Claude Code in `plugins/fb-scout/skills/`. After changing one, run
`uv run --no-project python scripts/sync_agent_skills.py` in the repository root to update the copies in
`.agents/skills/` for Cursor, Codex and Copilot (`--check` only reports; a test fails while they're out of
date).

---

## Responsible use

Automated collection is against Facebook's Terms, and posts contain personal
data. Use a dedicated research account and small volumes, and get your
university's ethics approval. Never publish raw screenshots with names. See
the checklist in [docs/PLAN.md](docs/PLAN.md#8-research-ethics-checklist-for-the-university).
For large-scale studies, consider applying for Meta's official **Meta Content
Library**, which is built for researchers.
