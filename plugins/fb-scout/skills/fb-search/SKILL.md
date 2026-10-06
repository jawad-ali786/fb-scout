---
name: fb-search
description: Search Facebook posts, group posts, comments or Marketplace listings for a keyword, brand or product. Keeps only results that really contain it, leaves out ads, job posts, giveaways and spam unless asked, screenshots each match with the keyword highlighted, and saves results.json with metadata (post URL, kind, author, group, time, text, price for listings, screenshot name). Can return only negative posts or complaints, after every result is read and labeled negative, neutral or positive. Use when the user asks to search, collect, monitor or find Facebook posts, group posts, comments, complaints, negative reviews or Marketplace listings about something.
argument-hint: "<keyword> [max=20] [group=<facebook group url> | marketplace [city=karachi] [details]] [match=phrase|all|any] [comments] [negative] [names] [include=promotion,job,giveaway,spam|all] [blur]"
allowed-tools: mcp__plugin_fb-scout_fb-scout__fb_status, mcp__plugin_fb-scout_fb-scout__fb_login, mcp__plugin_fb-scout_fb-scout__fb_search, mcp__plugin_fb-scout_fb-scout__fb_list_runs, mcp__plugin_fb-scout_fb-scout__fb_dataset_stats, mcp__plugin_fb-scout_fb-scout__fb_dataset_items, mcp__plugin_fb-scout_fb-scout__fb_label_queue, mcp__plugin_fb-scout_fb-scout__fb_label_items, Read
---

# Facebook keyword search (FB Scout)

User request: $ARGUMENTS

## 1. Work out the parameters

From the request, extract:
- `keyword` (required). If it's missing, ask for it and stop.
- `max_results`: `max=N`, default 20. Keep it at 50 or below unless the user insists. Low volume protects the account.
- `source`: `marketplace` if the user wants Marketplace / listings / items for sale; otherwise `posts`.
  - `marketplace_location`: `city=<name>` (e.g. `karachi`) if the user names a city; default is near the account.
  - `listing_details`: true if the user says `details` or wants descriptions, sellers or listing dates
    (opens every kept listing: slower, more page views).
- `group_url`: `group=<url>`, if the user wants to search inside one Facebook group (posts only).
- `match_mode`: `phrase` (default; the words in order, also joined by any symbols or written together: `solar-panel`, `Solar+Panel`, `#solarpanel`), `all` (every word, any order) or `any`.
- `include_comments`: true if the user says `comments` or asks about comments.
- `include_name_matches`: true only if the user says `names` or explicitly wants results where the keyword
  is only in a person's, page's or group's name (and profile / group-member cards). Default false.
- `only_negative`: true if the user wants only negative posts: "negative", "complaints", "bad reviews",
  "problems with", "what people dislike". See step 4.
- `include_types`: ads, job posts, giveaways and spam are left out by default. Pass a list only when the user
  wants them: `promotion` ("include ads", "sale posts", "price lists", "the brand's own posts"), `job`
  ("job posts too"), `giveaway`, `spam`, or `["all"]` ("everything", "don't filter"). Also `include=...`.
  Not needed for a Marketplace search: it always keeps its listings. (The dataset hides listings like ads;
  see `/fb-scout:fb-dataset`.)
- `blur_names`: true if the user says `blur`, or wants screenshots they can share or show.
- `output_dir`: only if the user names a folder.

Several keywords or several groups at once? Use `/fb-scout:fb-batch` instead.

**Negative-only searches.** A bare brand name mostly returns the brand's own ads, and complaints written
under those ads rarely repeat the brand name. Unless the user gave an exact search, prefer one of:
- the brand plus a complaint word with `match_mode: "all"`, e.g. `Inverex problem`, `Brand X complaint`,
  `Brand X kharab` (Roman Urdu), `Brand X warranty`;
- the brand inside a customer or buy/sell group (`group_url`), with `include_comments: true`;
- a larger `max_results` (30–50), because only part of the results will be negative.
Tell the user which search you chose and why.

## 2. Run the search right away

Call `fb_search` once with the parameters; don't call `fb_status` first (the search checks the login
itself, and every extra check opens a browser). It runs in a hidden (headless) browser, so no window
appears. Tell the user it takes about 5 seconds per saved result (a 20-post search takes 1–2 minutes;
comments add more). A bare brand name mostly finds ads, which are skipped, so such a search scrolls
further and can take longer or save fewer posts than asked. Pass `show_browser: true` only if the user
asks to watch the run or you are debugging.

If the fb-scout tools aren't available at all, FB Scout is still setting itself up in the background on
this computer (the first start installs uv, Python and packages, 1–2 minutes). A note at the start of the
session says so, and you'll be told when it's done. Tell the user exactly that; don't install anything
yourself. When setup is done, `/mcp reconnect all` makes the tools available without restarting (the
note says when a restart is needed instead).

## 3. Log in only when the search says so

If `fb_search` returns `error` = `not_logged_in`, log in once with `fb_login`, then run the same
`fb_search` again in this same turn, so the user doesn't need another prompt. Pick the login method
from what the user said:
- **Normal Chrome window** (default, `method: "browser"`). First tell the user: "A normal Chrome window will open. Log in with your research Facebook account (not your personal one), wait until you see your feed, then **close the window**. After that, searches run hidden." The tool waits until they close it.
- **Copy from Firefox** (`method: "firefox"`): if the user says they're already logged in to Facebook in Firefox. No window opens. Whatever account is logged in there will be used.
- **Cookie file** (`method: "cookie_file"`, `cookie_file: <path>`): if the user exported cookies from any browser with an extension. Remind them to delete that file afterwards, because it works like a password.

If login fails or times out, explain the `message` / `hint` and stop. If a tool returns
`browser_unavailable`, tell the user to install Google Chrome (or Microsoft Edge) and stop.

Rules:
- Run only **one** search at a time. Never call `fb_search` in parallel.
- If the result has `error` = `checkpoint` or `blocked`, **stop**. Tell the user to resolve it in the browser and wait (hours for `blocked`). Do not retry.
- If `error` = `not_logged_in` again after a successful login, stop and show the `message`.
- If `error` = `busy` or `profile_in_use`, another run or window is open. Ask the user to close it.
- If a hidden run finds 0 candidates but the login is fine, try once more with `show_browser: true`. Facebook sometimes treats hidden browsers differently.

## 4. Negative only: label, then filter

Skip this step unless `only_negative` was set. The result then has a `next_step`.

1. Call `fb_label_queue` with the run's `run_id` (and the same `include_types`, if the search had any;
   the `next_step` shows the exact call). It returns up to 20 items with their full text.
2. Read each text and decide its sentiment **towards the keyword** (brand/product/topic), following the
   rubric in the `fb_label_items` tool description:
   - **negative**: complaint, criticism, bad experience, defect or failure report, scam/fraud claim, refund or
     service problem, warning others, anger, switching away, sarcastic praise. Roman Urdu/Urdu cues:
     bekar, ghatiya, kharab, fraud, dhoka, paisay zaya, masla, شکایت, خراب.
   - **positive**: praise, recommendation, satisfaction.
   - **neutral**: ads, sale listings, price lists, announcements, questions without a complaint, news.
   - If a post was edited, the edit counts ("Edit: worst experience" makes it negative).
   - Judge from the text; `Read` the `screenshot_file` only when the text is unclear (e.g. text in an image).
3. Save them with one `fb_label_items` call per batch: `[{item_id, sentiment, reason}]`, where reason is
   one short sentence quoting the cue. Include `keyword` when the queue item shows one.
4. Repeat 1–3 until `remaining` is 0.
5. Report only `fb_dataset_items` with `run_id` and `sentiment: "negative"` (step 5). The other results stay
   saved with their labels; mention how many were neutral/positive in one line.
   If there are no negative results, say so plainly and suggest a complaint-oriented search (step 1).

## 5. Report

- Where the files are: `run_dir`, which has `results.json` and `screenshots/`.
- Counts: `stats.verified`, `stats.saved`, how many of each `kind`, `stats.comments_saved`
  (Marketplace: `stats.listings_opened`).
- What was left out: `filtered_out` in one line, e.g. "Left out 14 ads and 1 job post; say 'include ads' to
  keep them." (Examples with the reason are in `results.json` under `run.filtered_examples`.) Records the
  user asked to keep have a `content_type`; mark them in the table.
- A short table of the saved records: kind, author or group, posted date, language, snippet, screenshot name.
  Listings: title, price, location (and seller, condition when details were opened).
  Negative-only: date, group, author, the label's reason, snippet.
  Mark records with `matched_in: "image_text"` (keyword only in the picture), `"name"` (only in a name) or
  `"profile"` (a profile / member card).
- The dataset: from `dataset`, how many records were new and how many were already known from earlier
  runs (`merged`). If `dataset.ok` is false, say the run itself is fine and suggest `fb_import_runs` later.
- Any `warnings`, in plain words.
- If little or nothing was saved because most results were left out (`filtered_out`), say so plainly: a
  bare brand name mostly finds the brand's own posts and dealers' ads. Offer a search for what people say
  (`<brand> problem` / `<brand> kharab` with `match=all`, or inside a buy/sell or users' group), or to
  run it again with those posts included.
- If nothing was verified, say how many candidates Facebook returned. Suggest `match=all`, another spelling, or searching inside a specific group. Mention the `debug/` folder in the run directory if it exists.

Optionally `Read` one or two screenshots to confirm the keyword is visible. Never make up records, URLs or
labels. Report only what the tools returned.

## Notes

- Results are saved as `<output>/<keyword>/<timestamp>/results.json` plus `screenshots/*.png`, and every
  run is added to the dataset `<output>/fbscout.sqlite` (duplicates across runs merged). Labels are kept in
  the dataset and in `labels.json`. For questions about everything collected so far, use `/fb-scout:fb-dataset`.
- This is a research tool. Collect only what the research needs, and don't share screenshots that contain people's names publicly.
