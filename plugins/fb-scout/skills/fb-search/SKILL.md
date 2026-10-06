---
name: fb-search
description: Search Facebook for a keyword, brand or product. Keeps only posts/comments that really contain it, screenshots each match with the keyword highlighted, and saves results.json with metadata (post URL, kind such as post/group post/comment/reel, author, group, time, text, screenshot name). Use when the user asks to search, collect, monitor or find Facebook posts, group posts or comments about something.
argument-hint: "<keyword> [max=20] [group=<facebook group url>] [match=phrase|all|any] [comments] [blur]"
allowed-tools: mcp__plugin_fb-scout_fb-scout__fb_status, mcp__plugin_fb-scout_fb-scout__fb_login, mcp__plugin_fb-scout_fb-scout__fb_search, mcp__plugin_fb-scout_fb-scout__fb_list_runs, mcp__plugin_fb-scout_fb-scout__fb_dataset_stats, Read
---

# Facebook keyword search (FB Scout)

User request: $ARGUMENTS

## 1. Work out the parameters

From the request, extract:
- `keyword` (required). If it's missing, ask for it and stop.
- `max_results`: `max=N`, default 20. Keep it at 50 or below unless the user insists. Low volume protects the account.
- `group_url`: `group=<url>`, if the user wants to search inside one Facebook group.
- `match_mode`: `phrase` (default; the words in order, also joined by any symbols: `solar-panel`, `Solar+Panel`, `solar_panel`), `all` (every word, any order) or `any`.
- `include_comments`: true only if the user says `comments` or asks about comments.
- `blur_names`: true if the user says `blur`, or wants screenshots they can share or show.
- `output_dir`: only if the user names a folder.

Several keywords or several groups at once? Use `/fb-scout:fb-batch` instead.

## 2. Make sure the browser is logged in

1. Call `fb_status`.
2. If `logged_in` is false, log in once with `fb_login`. Pick the method from what the user said:
   - **Normal Chrome window** (default, `method: "browser"`). First tell the user: "A normal Chrome window will open. Log in with your research Facebook account (not your personal one), wait until you see your feed, then **close the window**. After that, searches run hidden." The tool waits until they close it.
   - **Copy from Firefox** (`method: "firefox"`): if the user says they're already logged in to Facebook in Firefox. No window opens. Whatever account is logged in there will be used.
   - **Cookie file** (`method: "cookie_file"`, `cookie_file: <path>`): if the user exported cookies from any browser with an extension. Remind them to delete that file afterwards, because it works like a password.
3. If login fails or times out, explain the `message` / `hint` and stop.
4. If `fb_status` returns `browser_unavailable`, tell the user to install Google Chrome (or Microsoft Edge) and stop.

## 3. Run the search

Call `fb_search` once with the parameters. It runs in a hidden (headless) browser, so no window appears. Tell the user it takes about 5–15 seconds per saved post. Pass `show_browser: true` only if the user asks to watch the run or you are debugging.

Rules:
- Run only **one** search at a time. Never call `fb_search` in parallel.
- If the result has `error` = `checkpoint` or `blocked`, **stop**. Tell the user to resolve it in the browser and wait (hours for `blocked`). Do not retry.
- If `error` = `not_logged_in`, go back to step 2 once.
- If `error` = `busy` or `profile_in_use`, another run or window is open. Ask the user to close it.
- If a hidden run finds 0 candidates but the login is fine, try once more with `show_browser: true`. Facebook sometimes treats hidden browsers differently.

## 4. Report

- Where the files are: `run_dir`, which has `results.json` and `screenshots/`.
- Counts: `stats.verified`, `stats.saved`, how many of each `kind`, `stats.comments_saved`.
- A short table of the saved records: kind, author or group, posted date, language, snippet, screenshot name.
  Mark records with `matched_in: "image_text"`: the keyword is only in the picture (Facebook's reading of it).
- The dataset: from `dataset`, how many records were new and how many were already known from earlier
  runs (`merged`). If `dataset.ok` is false, say the run itself is fine and suggest `fb_import_runs` later.
- Any `warnings`, in plain words.
- If nothing was verified, say how many candidates Facebook returned. Suggest `match=all`, another spelling, or searching inside a specific group. Mention the `debug/` folder in the run directory if it exists.

Optionally `Read` one or two screenshots to confirm the keyword is visible. Never make up records or URLs. Report only what the tool returned.

## Notes

- Results are saved as `<output>/<keyword>/<timestamp>/results.json` plus `screenshots/*.png`, and every
  run is added to the dataset `<output>/fbscout.sqlite` (duplicates across runs merged). For questions
  about everything collected so far, use `/fb-scout:fb-dataset`.
- This is a research tool. Collect only what the research needs, and don't share screenshots that contain people's names publicly.
