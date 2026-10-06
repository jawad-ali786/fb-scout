# FB Scout — Full Project Plan

> University research project: collect Facebook content that mentions a keyword
> (brand, product, topic), keep screenshot evidence + structured metadata, and
> later run sentiment / churn / product-feedback analysis on it.

---

## 1. Goal

Build an **AI-agent plugin** that anyone can install on their own machine and
run with one command:

```
/fb-scout:fb-search "Brand X"
```

The agent searches Facebook, keeps only results that really contain the
keyword, takes a screenshot of each matching post/comment, and saves a
`results.json` file with metadata (URL, kind, author, group, text, screenshot
name...). Later phases turn that dataset into brand insights (sentiment, churn
signals, product complaints).

### Research questions it should eventually support

1. What do people say about brand X on Facebook (positive / negative / neutral)?
2. Which product aspects get complaints (price, quality, delivery, service)?
3. Are there **churn signals** ("switching to Y", "cancelling", "never again")?
4. How does sentiment change over time or after an event (launch, price change)?

---

## 2. Design principles

| Principle | What it means here |
|---|---|
| **Portable** | Ships as a Claude Code plugin. Another machine needs only Claude Code, `uv` and Google Chrome. No manual Python setup. |
| **Vendor-neutral** | The real work lives in an **MCP server**. Claude Code, Claude Desktop, Cursor, ChatGPT/Gemini clients with MCP support can all call the same tools. A plain CLI also exists for running it with no AI at all. |
| **Deterministic core, AI on top** | Browser navigation, extraction, matching and screenshots are normal code, so runs are reproducible (important for research). The AI picks parameters, handles problems, summarises, and later does the analysis. |
| **Human in the loop for login** | The user logs in by hand in a dedicated browser profile. The tool never stores passwords, never solves CAPTCHAs, and stops if Facebook shows a checkpoint. |
| **Polite and low volume** | Human-like pacing, small result caps, dedicated research account. Avoids getting the account banned. |
| **Hidden by default** | Only the one-time login shows a window. Searches run headless (`show_browser` to watch). |
| **Evidence first** | Every record links to a screenshot with the keyword highlighted, plus the raw text, so findings can be checked later. |

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Agent layer                                                     │
│  Claude Code plugin skill  /fb-scout:fb-search <keyword>         │
│  (or any MCP client: Claude Desktop, Cursor, other vendors)      │
└───────────────┬──────────────────────────────────────────────────┘
                │ MCP (stdio)
┌───────────────▼──────────────────────────────────────────────────┐
│  MCP server  (fbscout-mcp)                                       │
│  tools: fb_status · fb_login · fb_search · fb_batch ·            │
│         fb_list_runs · fb_dataset_stats · fb_dataset_items ·     │
│         fb_export · fb_exclude_items · fb_import_runs            │
└───────────────┬──────────────────────────────────────────────────┘
                │ Python API (also used by the `fbscout` CLI)
┌───────────────▼──────────────────────────────────────────────────┐
│  Core library  fbscout                                           │
│  browser.py  → Playwright + real Chrome, persistent profile      │
│  scraper.py  → search, scroll, collect, open posts, comments     │
│  extract.py  → in-page JS: post text, links, author, group       │
│  urls.py     → permalink cleaning + kind classification          │
│  matching.py → keyword verification (phrase / all / any)         │
│  capture.py  → keyword highlight + element screenshot (+ blur)   │
│  storage.py  → run folder, results.json                          │
│  dataset.py  → SQLite dataset, dedupe across runs      (Phase 2) │
│  dates.py · lang.py · batch.py · export.py             (Phase 2) │
└───────────────┬──────────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────────┐
│  Storage                                                         │
│  fb-scout-output/<keyword>/<timestamp>/results.json              │
│                                       /screenshots/*.png         │
│  fb-scout-output/fbscout.sqlite   (all runs, deduplicated)       │
│                 /_exports/  /_batches/                           │
└───────────────┬──────────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────────┐
│  Analysis (Phase 3+)                                             │
│  language detect → sentiment → aspects → churn intent → reports  │
└──────────────────────────────────────────────────────────────────┘
```

### Why these technologies

- **Python**: best ecosystem for the later NLP/sentiment work (pandas,
  transformers, scikit-learn) and for research notebooks.
- **Playwright + installed Google Chrome** (`channel="chrome"`): the real
  browser, with no browser download. Login happens once in a visible window.
  Searches then run in Chrome's headless mode using the same saved profile.
- **Why logged in:** a probe on 2026-10-02 found that Facebook's keyword search
  returns "Not Found" when logged out. Public Pages showed only 3–5 posts before
  a login wall. Search engines blocked automated discovery: DuckDuckGo showed a
  CAPTCHA, and Bing ignored the `site:` filter.
- **Persistent profile** in the plugin data folder: log in once, reuse the
  session. The profile is kept outside the code folder, so sharing the code
  never shares your Facebook cookies.
- **uv**: installs Python and the dependencies automatically on first run,
  so the plugin works the same on every machine.
- **MCP (Model Context Protocol)**: the open standard for agent tools, so the
  tools aren't tied to one AI vendor.

---

## 4. How it runs on any machine

**Prerequisites (one-time):**
1. Claude Code
2. `uv`. Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`.
   macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
3. Google Chrome

**Install the plugin:**
```
/plugin marketplace add jawad-ali786/fb-scout      # or a local folder path
/plugin install fb-scout@fb-scout-marketplace
```

**First use:**
```
/fb-scout:fb-search "keyword"
```
→ the agent sees you're not logged in → opens Chrome → you log in by hand →
the search runs → the results folder is shown.

**Without Claude Code:** add the MCP server to any MCP client, or use the CLI:
```
uv run --directory <plugin>/server fbscout login
uv run --directory <plugin>/server fbscout search "keyword" --max 20
```

---

## 5. Data model

### Record (one per matching post or comment)

| Field | Example | Notes |
|---|---|---|
| `id` | `r_3f2a9c1b7d4e` | stable hash of the URL (or of the text if there's no URL) |
| `kind` | `group_post` | `post`, `group_post`, `comment`, `reply`, `reel`, `video`, `photo`, `event`, `marketplace`, `unknown` |
| `keyword` | `solar panel` | |
| `keyword_verified` | `true` | the keyword really is in the text (Facebook search is fuzzy) |
| `matched_terms` | `["solar panel"]` | |
| `match_snippet` | `…need a solar panel installer in…` | |
| `post_url` | `https://www.facebook.com/groups/123/posts/456/` | tracking parameters removed |
| `comment_url` | `null` | set for comments |
| `author_name`, `author_url` | | |
| `group_name`, `group_url` | | for group posts |
| `time_text` | `3d` | as shown by Facebook (raw); `null` when Facebook draws it unreadably |
| `time_exact` | `Thursday 1 October 2026 at 10:37` | full date from the timestamp's hover tooltip |
| `posted_at`, `posted_date` | `2026-10-01T10:37:00+05:00` | parsed from `time_exact`, else `time_text` (v0.2) |
| `posted_at_precision` | `minute` | `minute` … `year` (`3d` is only day-precise) |
| `text` | full post or comment text | |
| `image_text` | `May be an image of text that says '…'` | Facebook's own reading of the images (v0.2) |
| `matched_in` | `text` | where the keyword was found: `text`, `full_text` or `image_text` |
| `language` | `ur-Latn` | `en`, `ur`, `ur-Latn` (Roman Urdu), `tl`, … (v0.2) |
| `screenshot_name` | `003_group_post_3f2a9c1b.png` | |
| `screenshot_path` | `screenshots/003_group_post_3f2a9c1b.png` | relative to the run folder |
| `source` | `search:posts` | `search:posts`, `search:group`, `comments` |
| `search_rank` | `3` | position in the search results |
| `captured_at` | ISO 8601 UTC | |

### Run metadata
Keyword, match mode, search scope, start/end time, tool version, stats
(candidates seen, verified, saved, screenshots, errors) and warnings.

---

## 6. Phased roadmap

### Phase 0: Setup ✅
- Repo layout, plugin manifest, marketplace file, plan docs.

### Phase 1: MVP ✅ (v0.1, see [MVP.md](MVP.md))
- Manual login with a saved profile
- Keyword search: global Posts search + search inside one group
- Scroll, collect, expand "See more", check the keyword is really there
- Kind classification (post / group post / reel / video / photo / ...)
- Element screenshot with the keyword highlighted
- `results.json` per run
- Optional, experimental: scan comments of the posts found
- Claude Code skill + MCP tools + CLI

Tested live on 2026-10-06 (results in §9): 4 keywords, global and in-group search,
comments. The tests found and fixed three bugs (v0.2): comments of a feed post behind
the post's dialog, matches only in a group/page/author name, and member cards saved
as posts. Still open: installing from GitHub on a second machine with Claude Code.

### Phase 2: Data quality and scale ✅ (v0.2, see [DATASET.md](DATASET.md))
- ✅ **SQLite dataset** across runs: dedupe by stable URL, comment id, or group + text
  fingerprint (pfbid links change per session), `first_seen` / `last_seen` / `times_seen`
- ✅ **Studies**: a list of keywords × a list of groups, one search at a time, stops on
  checkpoints; scheduled through Task Scheduler / cron
- ✅ Better comment coverage: open collapsed comments, switch to "All comments", expand
  replies, exact comment times; all inside the post's own container
- ✅ `posted_at` from the tooltip or from `time_text` ("3d", "Yesterday at 10:00", ...)
  with the browser's UTC offset and a precision field
- ✅ Text inside images: Facebook's own image text (alt text) is kept and searched.
  A local OCR engine was left out (large download for every user); screenshots can be
  read in Phase 3 if needed
- ✅ Language detection: `en`, `ur`, `ur-Latn` (Roman Urdu), `tl`, `ar`, `hi`, ... (rule-based)
- ✅ Anonymisation: pseudonymous `author_id` + URL removal in exports; `blur_names` blurs
  names and profile pictures in screenshots (faces in photos are not blurred)
- ✅ CSV (Excel-ready) / JSONL / Parquet export with filters

### Phase 3: Sentiment and brand analysis
- **Sentiment** (positive / negative / neutral) per record
- **Aspect-based sentiment**: price, quality, delivery, customer service, etc.
- **Churn intent detection**: "switching to", "cancel", "never buying again",
  "any alternative to X?"
- **Product feedback classification**: bug/defect, feature request, praise, question
- Two interchangeable approaches to compare (good for a research paper):
  1. LLM classification (Claude with a fixed rubric + structured JSON output)
  2. Fine-tuned / pre-trained multilingual model (e.g. XLM-R based sentiment)
- **Evaluation**: hand-label a gold set (e.g. 300–500 records, 2 annotators,
  Cohen's kappa), then report precision/recall/F1 per class.

### Phase 4: Reporting
- Brand dashboard: sentiment over time, top complaints, churn mentions
- Exportable report for the thesis (charts + example screenshots as evidence)

---

## 7. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Facebook ToS forbids automated collection | Account restriction; ethics concerns | Dedicated research account, low volume, human pacing, ethics approval, consider applying for **Meta Content Library** (the official researcher API) |
| Bot detection / checkpoint / "temporarily blocked" | Run fails, account locked | Real Chrome, random delays, small caps, dedicated account; detect checkpoint pages and **stop**, hand control back to the human; switch to `show_browser` if headless runs get flagged |
| Facebook HTML changes often | Extraction breaks | Use roles/ARIA, `data-ad-preview` and URL patterns, not CSS class names; selectors in one place; the agent reports breakage |
| Facebook UI language | "See more" / "Comment by" labels differ | Set the research account language to English; label lists in one place |
| Facebook search is incomplete and personalised | Biased sample | Document as a limitation; mix global search with fixed group lists; record exactly what was searched and when |
| Comments are not searchable directly | Missed mentions | Open the posts found and scan their comments (slow, so capped) |
| Personal data in screenshots | Privacy/GDPR, ethics | Data minimisation, secure storage, no public sharing of raw screenshots, anonymisation in Phase 2 |
| Long tool runs | Agent timeouts | Per-run caps; stdio MCP server with an explicit timeout; CLI for big runs |

---

## 8. Research ethics checklist (for the university)

- [ ] Get ethics / IRB approval for collecting social media data
- [ ] Collect only public posts and groups the research account legitimately joined
- [ ] Don't collect from private profiles; don't message or interact with users
- [ ] Store data encrypted or on university storage; limit who can access it
- [ ] Anonymise authors in any published results; don't publish raw screenshots with names
- [ ] Keep a data retention / deletion plan
- [ ] Note in the paper that Facebook search is personalised and incomplete (sampling bias)
- [ ] Consider Meta Content Library for any large-scale follow-up

---

## 9. How success is measured

| Metric | Target for MVP | Result (2026-10-06, after fixes) |
|---|---|---|
| Keyword precision (saved records that truly contain the keyword) | ≥ 95% | **40/40** (first run before the fixes: ~76%, see below) |
| Field accuracy (URL, kind, author correct on a manual check of 50 records) | ≥ 90% | URL **10/10** opened the same post; author/group/kind **8/8** on screenshots; 0 missing fields in 40 records |
| Run success rate (runs that finish without crashing) | ≥ 90% | **10/10** runs |
| Screenshots that clearly show the post and highlight the keyword | ≥ 90% | **11/11** checked by eye |
| Time per saved post | < 15 s | **4.7–5.6 s** |

How it was measured: keywords `inverter`, `Longi`, `Jinko` (plus `solar panel` earlier), each
in global search and inside one solar group, 10 posts per search, as two studies
(`fbscout batch`). Precision and completeness were computed from `results.json`;
screenshots were checked by eye; a random sample of saved URLs was opened again to see
whether it shows the same post. The URL and screenshot samples are smaller than the 50
records in the target, so repeat the hand check on a larger sample for the thesis.

The first study found two precision bugs, both fixed in v0.2:
1. Facebook search also matches the **group's, page's or author's name**. Posts in a group
   called "all type solar inverter sale purchase" were saved for `inverter` without
   mentioning it. Now names (and comment previews) are ignored when checking the keyword;
   attachments such as a sale listing's title still count.
2. A search inside a group lists **matching members** first. These person cards were saved as
   group posts. Now anything without a permalink, time and message is skipped
   (`stats.skipped_not_posts`).

---

## 10. Suggested timeline (one semester)

| Weeks | Work |
|---|---|
| 1–2 | MVP: login, search, extract, screenshot, JSON (this repo) |
| 3 | Test on 5–10 real keywords, fix extraction, measure the metrics above |
| 4–5 | Phase 2: SQLite dataset, group lists, comment coverage, dedup |
| 6 | Ethics review deliverables, anonymisation |
| 7–9 | Phase 3: label a gold set, LLM vs model sentiment, churn detection |
| 10–11 | Phase 4: dashboard and report |
| 12 | Write-up |
