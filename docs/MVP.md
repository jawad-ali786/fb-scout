# FB Scout — MVP Specification

The smallest version that is useful for the research: **give a keyword → get
screenshots + a JSON file of the Facebook posts that really contain it.**
Sentiment and analysis come later (see [PLAN.md](PLAN.md), Phase 3).

---

## 1. Scope

### In scope
| # | Feature |
|---|---|
| 1 | One-time **login** into a dedicated FB Scout profile, reused on later runs. Three ways: a normal (non-automated) Chrome window, copying the login from Firefox, or importing an exported cookie file. **Searches run headless** (hidden) by default |
| 2 | **Global keyword search** (Facebook "Posts" search results) |
| 3 | **Group-scoped search**: search inside one group by its URL (the account must be able to see the group) |
| 4 | Scroll through results, expand "See more", extract text, links, author, group, time |
| 5 | **Keyword check**: keep only results whose text really contains the keyword (`phrase` / `all` words / `any` word). Between the words, any characters that are not letters or digits count, or none at all (`solar-panel`, `Solar+Panel`, `solar_panel`, `solarpanel`, `#solarpanel`); the phrase must still be whole words (`solar panels`, `mysolarpanel` don't match). Side effect: a sentence break also counts ("go solar. Panel prices…" matches `solar panel`) |
| 6 | **Kind classification** from the permalink: `post`, `group_post`, `reel`, `video`, `photo`, `event`, `marketplace`, `comment`, `reply`, `unknown` |
| 7 | **Element screenshot** of each matching post, keyword highlighted in yellow |
| 8 | **`results.json`** per run (run metadata + one record per match), written as results come in so a crash keeps partial data |
| 9 | *Experimental:* open the first N matching posts and capture **comments** that contain the keyword |
| 10 | Stop safely on login pages, checkpoints and "temporarily blocked" pages |
| 11 | Debug dump (full-page screenshot + HTML) when nothing is found, to fix selectors |
| 12 | Packaged as a **Claude Code plugin** (skill + MCP server) **and** a plain **CLI** |

### Out of scope (later phases)
- Sentiment, aspects, churn detection (Phase 3)
- Database across runs, scheduling, lists of groups, date parsing, language, image text,
  exports: **added in v0.2 (Phase 2)**, see [DATASET.md](DATASET.md)
- Video transcripts
- Anti-detection tricks: no CAPTCHA solving, no proxy rotation, no fingerprint spoofing

### Changes in v0.2 that affect the search itself
- Each record also has `posted_at` / `posted_date` / `posted_at_precision` / `posted_at_source`,
  `image_text` and `language`. The run has `browser_timezone` / `browser_utc_offset_minutes`.
  Group searches store the group's name from the page title.
- The keyword check also looks at Facebook's image text: `matched_in: "image_text"`.
- Comments: the post's own container is found first, because a group post link can open
  the post in a dialog over the home feed. Inside it, collapsed comments are opened, the
  order is switched to "All comments", replies are expanded, and each saved comment
  gets its exact time from the hover tooltip. If the post can't be found on its page,
  its comments are skipped with a warning.
- `blur_names`: names and profile pictures blurred in screenshots.
- After the run, the run is added to the dataset; the summary has a `dataset` entry.

---

## 2. User flow

```
User: /fb-scout:fb-search "solar panel" max=15
  │
  ├─ agent → fb_status            → not logged in
  ├─ agent → fb_login             → Chrome opens once, user logs in by hand
  ├─ agent → fb_search(keyword="solar panel", max_results=15)
  │            hidden Chrome opens search page, scrolls, extracts, screenshots
  │            writes fb-scout-output/solar-panel/20261001-103000/
  └─ agent → reports: 12 matches (9 post, 3 group_post), folder path,
             table of results, any warnings
```

---

## 3. MCP tools

| Tool | Parameters | Returns |
|---|---|---|
| `fb_status` | — | `logged_in`, browser in use, profile folder, default output folder |
| `fb_login` | `method="browser"` (`"firefox"`, `"cookie_file"`), `cookie_file=None`, `timeout_seconds=600`, `force=False` | normal Chrome window: waits until you close it, then checks the login. Firefox / cookie file: copies facebook.com cookies into the profile and checks them |
| `fb_search` | `keyword` (required), `max_results=20` (max 100), `group_url=None`, `match_mode="phrase"`, `include_comments=False`, `max_comment_posts=5`, `output_dir=None`, `save_unverified=False`, `highlight=True`, `max_minutes=10`, `show_browser=False` | run folder, `results.json` path, stats, compact list of records, warnings |
| `fb_list_runs` | `output_dir=None`, `keyword=None`, `limit=20` | previous runs with stats |

The same functions are available through the CLI:

```
fbscout status
fbscout login
fbscout search "solar panel" --max 15 [--group URL] [--match all] [--comments]
fbscout runs
```

---

## 4. Output

```
fb-scout-output/
└── solar-panel/
    └── 20261001-103000/
        ├── results.json
        ├── screenshots/
        │   ├── 001_post_3f2a9c1b.png
        │   ├── 002_group_post_8be01d77.png
        │   └── ...
        └── debug/            (only when nothing was found or on error)
```

`results.json`:
```json
{
  "run": {
    "tool": "fb-scout", "version": "0.1.0",
    "run_id": "solar-panel_20261001-103000",
    "keyword": "solar panel", "match_mode": "phrase",
    "scope": "search:posts", "group_url": null,
    "started_at": "2026-10-01T10:30:00Z", "finished_at": "2026-10-01T10:33:12Z",
    "status": "completed",
    "stats": {"candidates_seen": 31, "verified": 12, "saved": 12, "screenshots": 12, "comments_saved": 0, "errors": 0},
    "warnings": []
  },
  "results": [
    {
      "id": "r_8be01d77c2a4",
      "kind": "group_post",
      "keyword": "solar panel",
      "keyword_verified": true,
      "matched_terms": ["solar panel"],
      "match_snippet": "…anyone know a good solar panel installer near…",
      "post_url": "https://www.facebook.com/groups/123456/posts/789012/",
      "comment_url": null,
      "parent_post_url": null,
      "author_name": "Ali Khan",
      "author_url": "https://www.facebook.com/groups/123456/user/1000123/",
      "group_name": "Solar Users Pakistan",
      "group_url": "https://www.facebook.com/groups/123456/",
      "time_text": "3d",
      "time_exact": "Sunday, September 28, 2026 at 4:12 PM",
      "text": "Anyone know a good solar panel installer near Lahore? ...",
      "screenshot_name": "002_group_post_8be01d77.png",
      "screenshot_path": "screenshots/002_group_post_8be01d77.png",
      "source": "search:posts",
      "search_rank": 2,
      "captured_at": "2026-10-01T10:30:41Z"
    }
  ]
}
```

---

## 5. Acceptance criteria

Status after the live test on 2026-10-06 (see [PLAN.md §9](PLAN.md#9-how-success-is-measured)):

- [ ] A fresh machine with Claude Code + uv + Chrome can install the plugin and run a search with no manual Python setup
  (partly checked: a clean clone from GitHub with an empty uv cache installs in ~15 s, all tests pass and the MCP server
  starts with its 9 tools; still to do: `/plugin marketplace add jawad-ali786/fb-scout` + a search on a second computer)
- [x] Login is needed only once; later runs reuse the session
- [x] For a common keyword, the run saves up to `max_results` verified matches (10/10 in all 10 test runs)
- [x] Every saved record has `kind`, `screenshot_name`, and `post_url` (or a warning explaining why the URL is missing)
- [x] Screenshots show only that post, with the keyword highlighted (checked by eye on a sample)
- [x] Tracking parameters (`__cft__`, `__tn__`, ...) are removed from URLs
- [x] Checkpoint / login / block pages stop the run with a clear message, and partial results are kept (offline tests; not triggered live on purpose)
- [x] No passwords or cookies are written to the output folder or the code folder
- [x] Unit tests pass (`uv run pytest`), including the offline browser test on a fake Facebook page

---

## 6. Test plan

| Level | What | How |
|---|---|---|
| Unit | URL cleaning, kind classification, keyword matching, run writer | `pytest` |
| Offline browser | Extraction JS, "See more", highlight, element screenshots on a local fake Facebook page | `pytest` (uses installed Chrome, headless) |
| Live smoke | 3 keywords × (global, one group) on the research account | manual run, check 20 records by hand |
| Metrics | Precision, field accuracy, success rate (PLAN.md §9) | spreadsheet from `results.json` |

---

## 7. Known limitations of the MVP

- Facebook search is personalised and incomplete. It's a sample, not a census.
- Comment capture is limited: up to 20 matching comments per post and 15 "View more" clicks, on the first `max_comment_posts` posts (max 20).
- Built for an **English** Facebook UI. Set the research account's language to English.
- Facebook changes its HTML often. If extraction breaks, check the `debug/` folder and update `extract.py`.
- Use a dedicated research account and keep volumes low. Automated collection is against Facebook's terms. See the ethics checklist in PLAN.md §8.
