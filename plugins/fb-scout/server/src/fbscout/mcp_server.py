"""MCP server (stdio). Works with Claude Code, Claude Desktop, Cursor and any other MCP client."""

from __future__ import annotations

import logging
import sys
from typing import Literal

from mcp.server.mcpserver import Context, MCPServer

from . import __version__, api
from .batch import Study
from .scraper import SearchOptions

# stdout carries the MCP protocol: all logging must go to stderr.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s fbscout %(levelname)s %(message)s")

mcp = MCPServer(
    "fb-scout",
    version=__version__,
    instructions=(
        "Facebook keyword research tools. Typical flow: fb_status → (fb_login if not logged in) → fb_search, "
        "or fb_batch for several keywords/groups/Marketplace. Every run is added to a SQLite dataset (deduplicated "
        "across runs): read it with fb_dataset_stats / fb_dataset_items, export it with fb_export. Promotions, "
        "job posts, giveaways and spam are left out everywhere, and Marketplace listings are hidden in the dataset, "
        "unless include_types asks for them. For negative "
        "posts only: label items with fb_label_queue + fb_label_items, then filter sentiment='negative'. "
        "Only one browser run at a time. If a tool returns error 'checkpoint' or 'blocked', stop and tell the "
        "user; never retry in a loop."
    ),
)


def _progress_reporter(ctx: Context | None):
    async def progress(message: str, done: int, total: int) -> None:
        if ctx is None:
            return
        try:
            await ctx.report_progress(done, total or None, message)
        except Exception:  # progress is best-effort; never fail the run because of it
            pass
    return progress


@mcp.tool()
async def fb_status() -> dict:
    """Check whether the FB Scout browser profile is logged in to Facebook, which browser is used,
    and where results are saved. Fast; opens a hidden browser for a few seconds."""
    return await api.status()


@mcp.tool()
async def fb_login(
    method: Literal["browser", "firefox", "cookie_file"] = "browser",
    cookie_file: str | None = None,
    timeout_seconds: int = 600,
    force: bool = False,
) -> dict:
    """Get a Facebook login into the FB Scout profile. Searches afterwards run hidden (headless).

    - method 'browser' (default): opens the user's real Chrome/Edge as a NORMAL window (not automated)
      on the Facebook login page. The user logs in with a dedicated research account, then CLOSES the
      window themselves; the tool waits for that (up to timeout_seconds) and checks the login.
    - method 'firefox': copies the Facebook login already present in the user's Firefox (no window).
    - method 'cookie_file': imports a cookies.txt / JSON export (from any browser) at `cookie_file`.
    - force: open the login window even if already logged in (e.g. to switch account).
    Whatever account is logged in at the source is the one FB Scout will use."""
    return await api.login(method, cookie_file, timeout_seconds, force)


@mcp.tool()
async def fb_search(
    keyword: str,
    max_results: int = 20,
    source: Literal["posts", "marketplace"] = "posts",
    group_url: str | None = None,
    match_mode: Literal["phrase", "all", "any"] = "phrase",
    include_comments: bool = False,
    max_comment_posts: int = 5,
    include_name_matches: bool = False,
    marketplace_location: str | None = None,
    listing_details: bool = False,
    only_negative: bool = False,
    include_types: list[str] | None = None,
    output_dir: str | None = None,
    save_unverified: bool = False,
    highlight: bool = True,
    max_minutes: float = 10,
    show_browser: bool = False,
    blur_names: bool = False,
    ctx: Context | None = None,
) -> dict:
    """Search Facebook for `keyword`, keep only results that really contain it (in the text, or in the
    text Facebook read from the post's images), screenshot each match (keyword highlighted) and write
    results.json with metadata (post_url, kind, author, group, time, posted_at, language, text,
    screenshot_name). The run is also added to the dataset (see fb_dataset_stats).
    Promotions, job posts, giveaways and spam are left out (counted in `filtered_out`, examples with the
    reason in results.json) unless include_types keeps them; Marketplace listings are never filtered.

    - max_results: matches to save (1-100).
    - source: 'posts' (default; global Posts search, or inside `group_url`) or 'marketplace'
      (Marketplace listings whose title contains the keyword: title, price, location, listing URL).
    - group_url: search inside this group instead of global Posts search (posts only).
    - match_mode: 'phrase' (the words in order, joined by any symbols or written together: 'solar-panel',
      'Solar+Panel', '#solarpanel'), 'all' (every word), 'any' (at least one word).
    - include_comments: also open the first `max_comment_posts` matching posts, switch them to
      "All comments", expand replies and capture comments that contain the keyword (slower).
    - include_name_matches: also keep posts whose keyword is only in the author's / page's / group's
      name, and profile or group-member cards (kind 'profile'). Off by default: those are usually not
      about the keyword.
    - marketplace_location: Marketplace city (e.g. 'karachi') or location id; default near the account.
    - listing_details: open each kept listing for its description, seller, condition and date (slower).
    - only_negative: the user wants negative posts only. Everything is still saved; the result has a
      `next_step`: label the run with fb_label_queue / fb_label_items, then report only the negatives.
    - include_types: content left out by default that should be kept too: 'promotion' (ads, items or
      services for sale, price lists, the brand page's own posts), 'job', 'giveaway', 'spam', or 'all'.
      (A Marketplace search always keeps its listings.)
      Only when the user asks for those (e.g. "include ads", "job posts too").
    - output_dir: root folder for results (default: <project>/fb-scout-output).
    - save_unverified: also save results Facebook returned that do not contain the keyword.
    - blur_names: blur names and profile pictures of people/pages in the screenshots.
    - show_browser: run in a visible window instead of hidden (headless); use for debugging.
    Needs a Facebook login: if it returns not_logged_in, call fb_login and then this again. Takes about
    5 seconds per saved post (20 posts: 1-2 minutes; comments add about 30 s per scanned post).
    Returns the run folder, run_id, stats, warnings, a compact list of records and the dataset import result."""
    progress = _progress_reporter(ctx)

    opts = SearchOptions(
        keyword=keyword,
        max_results=max_results,
        group_url=group_url,
        match_mode=match_mode,
        include_comments=include_comments,
        max_comment_posts=max_comment_posts,
        output_dir=output_dir,
        save_unverified=save_unverified,
        highlight=highlight,
        headless=not show_browser,
        max_minutes=max_minutes,
        blur_names=blur_names,
        source=source,
        marketplace_location=marketplace_location,
        listing_details=listing_details,
        include_name_matches=include_name_matches,
        only_negative=only_negative,
        include_types=include_types or (),
    )
    return await api.search(opts, progress)


@mcp.tool()
async def fb_batch(
    study_file: str | None = None,
    keywords: list[str] | None = None,
    group_urls: list[str] | None = None,
    include_global: bool = True,
    include_marketplace: bool = False,
    marketplace_location: str | None = None,
    listing_details: bool = False,
    include_name_matches: bool = False,
    include_types: list[str] | None = None,
    max_results: int = 20,
    match_mode: Literal["phrase", "all", "any"] = "phrase",
    include_comments: bool = False,
    max_comment_posts: int = 5,
    max_minutes_per_search: float = 10,
    blur_names: bool = False,
    output_dir: str | None = None,
    dry_run: bool = False,
    ctx: Context | None = None,
) -> dict:
    """Run a study: every keyword in global search (if include_global), inside every group in
    `group_urls`, and on Marketplace (if include_marketplace), strictly one search at a time with a 1-3
    minute pause between searches. Stops at the first checkpoint/block/login problem. Each run is added to
    the dataset; a batch report is written to <output>/_batches/.

    Give either `study_file` (a JSON study file, see README) or `keywords` (+ optional `group_urls`).
    Use dry_run=true first to show the plan and the estimated time. Long: several minutes per search.
    Limit: 50 searches per batch. To keep only negative posts, label the batch afterwards
    (fb_label_queue with batch_id) and filter with sentiment='negative'. Promotions, job posts, giveaways
    and spam are left out unless include_types ('promotion', 'job', 'giveaway', 'spam' or 'all') keeps them.
    Marketplace listings (include_marketplace) are kept, but hidden in the dataset unless asked for."""
    try:
        if study_file:
            if keywords or group_urls:
                raise ValueError("Give either study_file or keywords/group_urls, not both.")
            study = Study.load(study_file)
        else:
            study = Study.from_dict({
                "name": "adhoc", "keywords": keywords or [], "groups": group_urls or [],
                "include_global": include_global, "include_marketplace": include_marketplace,
                "marketplace_location": marketplace_location, "listing_details": listing_details,
                "include_name_matches": include_name_matches, "include_types": include_types or [],
                "max_results": max_results,
                "match_mode": match_mode, "include_comments": include_comments,
                "max_comment_posts": max_comment_posts, "max_minutes_per_search": max_minutes_per_search,
                "blur_names": blur_names, "output_dir": output_dir,
            })
    except ValueError as exc:
        return {"ok": False, "error": "invalid_study", "message": str(exc)}
    if dry_run:
        return {"ok": True, "dry_run": True, **study.describe()}
    return await api.batch(study, _progress_reporter(ctx))


@mcp.tool()
def fb_list_runs(output_dir: str | None = None, keyword: str | None = None, limit: int = 20) -> dict:
    """List previous FB Scout runs (newest first) with their stats and folders."""
    return api.runs(output_dir, keyword, limit)


@mcp.tool()
def fb_dataset_stats(keyword: str | None = None, include_types: list[str] | None = None,
                     output_dir: str | None = None) -> dict:
    """Overview of the dataset (all runs, duplicates merged): number of distinct posts/comments, how
    many were seen in more than one run, and counts by keyword, kind, language, sentiment label, month
    posted and group. Promotions, job posts, giveaways, spam and Marketplace listings are not counted unless
    include_types names them ('promotion', 'job', 'giveaway', 'spam', 'marketplace' or 'all');
    `hidden_by_content_type` says how many."""
    return api.dataset_stats(output_dir, keyword, include_types)


@mcp.tool()
def fb_dataset_items(
    keyword: str | None = None,
    kind: str | None = None,
    language: str | None = None,
    sentiment: str | None = None,
    run_id: str | None = None,
    batch_id: str | None = None,
    group: str | None = None,
    since: str | None = None,
    until: str | None = None,
    contains: str | None = None,
    include_types: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
    full_text: bool = False,
    output_dir: str | None = None,
) -> dict:
    """Distinct posts/comments/listings from the dataset, newest post first, with posted_at, language,
    sentiment label and reason, author, group, price/location (listings), URLs, text (cut to 400
    characters unless full_text) and the absolute screenshot_file path.

    Filters: keyword; kind, language and sentiment as comma-separated lists (e.g. 'post,group_post',
    'ur,ur-Latn', 'negative'); run_id (items of one run) or batch_id (of one study); group (name/URL contains); since/until as
    YYYY-MM-DD on the posted date; contains (text, image text, price or location); include_types: also
    show promotions, job posts, giveaways, spam or Marketplace listings ('promotion', 'job', 'giveaway', 'spam',
    'marketplace' or 'all'; kind 'marketplace' also shows listings; hidden
    by default, counted in `hidden_by_content_type`; each item has content_type and content_reason).
    Page through with limit (max 500) and offset."""
    return api.dataset_items(output_dir, limit, offset, full_text, keyword=keyword, kind=kind, language=language,
                             sentiment=sentiment, run_id=run_id, batch_id=batch_id, group=group, since=since,
                             until=until, contains=contains, include_types=include_types)


@mcp.tool()
def fb_label_queue(
    run_id: str | None = None,
    keyword: str | None = None,
    batch_id: str | None = None,
    include_types: list[str] | None = None,
    limit: int = 20,
    output_dir: str | None = None,
) -> dict:
    """Items that still need a sentiment label (for the keyword that found them), with their text, so you
    can read and judge them. Narrow it to one run (run_id from fb_search), a keyword or a study (batch_id).
    Promotions, job posts, giveaways, spam and Marketplace listings are skipped unless include_types names them.
    `remaining` says how many are left in total. Save your judgements with fb_label_items."""
    return api.label_queue(output_dir, keyword, run_id, batch_id, limit, include_types)


@mcp.tool()
def fb_label_items(labels: list[dict], output_dir: str | None = None) -> dict:
    """Save sentiment labels: a list of {"item_id", "sentiment", "reason", "keyword"?}.
    sentiment is 'negative', 'neutral' or 'positive' TOWARDS THE KEYWORD (brand/product/topic):
    - negative: complaint, criticism, bad experience, defect/failure, scam/fraud claim, refund or service
      problem, warning others, anger, switching away ("never again", "switching to Y"), sarcastic praise.
      Roman Urdu/Urdu cues: bekar, ghatiya, kharab, fraud, dhoka, paisay zaya, worst, شکایت, خراب.
    - positive: praise, recommendation, satisfaction.
    - neutral: ads, sale listings, price lists, announcements, questions without an opinion, news,
      or an opinion about something else.
    reason: one short sentence quoting the cue (max 300 characters). keyword can be left out when the item
    was found by only one keyword. Labels are kept per item and keyword in the dataset and labels.json."""
    return api.label_items(labels, output_dir)


@mcp.tool()
def fb_export(
    format: Literal["csv", "jsonl", "parquet"] = "csv",
    anonymize: bool = False,
    keyword: str | None = None,
    kind: str | None = None,
    language: str | None = None,
    sentiment: str | None = None,
    run_id: str | None = None,
    batch_id: str | None = None,
    group: str | None = None,
    since: str | None = None,
    until: str | None = None,
    contains: str | None = None,
    include_types: list[str] | None = None,
    file: str | None = None,
    output_dir: str | None = None,
) -> dict:
    """Export the dataset (one row per distinct post/comment/listing) for Excel, pandas, R or SPSS, with
    sentiment labels, listing price/location/condition and all metadata.
    csv opens directly in Excel (UTF-8, Urdu works); parquet needs the optional pyarrow extra.
    anonymize=true replaces authors with stable pseudonyms (author_id) and drops all URLs; names inside
    the text and screenshots are not removed. Same filters as fb_dataset_items (e.g. sentiment='negative';
    promotions, job posts, giveaways, spam and Marketplace listings only with include_types).
    Default file: <output>/_exports/fbscout_<keyword|all>_<timestamp>.<format>."""
    return api.dataset_export(output_dir, format, file, anonymize, keyword=keyword, kind=kind, language=language,
                              sentiment=sentiment, run_id=run_id, batch_id=batch_id, group=group, since=since,
                              until=until, contains=contains, include_types=include_types)


@mcp.tool()
def fb_exclude_items(item_ids: list[str], reason: str, output_dir: str | None = None) -> dict:
    """Leave items (ids 'i_...' from fb_dataset_items) out of the dataset, e.g. false positives or posts
    that are not about the topic. Recorded with the reason in exclusions.json next to the dataset, so
    later imports keep them out; the run folders are not changed. Only do this when the user asked for it."""
    return api.dataset_exclude(item_ids, reason, output_dir)


@mcp.tool()
def fb_import_runs(output_dir: str | None = None) -> dict:
    """(Re)import every run folder in the output folder into the dataset. Safe to repeat: runs already
    imported are not counted twice. Needed only for runs made before v0.2 or copied in from elsewhere."""
    return api.dataset_import(output_dir)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
