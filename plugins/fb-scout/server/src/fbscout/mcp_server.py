"""MCP server (stdio). Works with Claude Code, Claude Desktop, Cursor and any other MCP client."""

from __future__ import annotations

import asyncio
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
        "across runs): read it with fb_dataset_stats / fb_dataset_items, export it with fb_export. For negative "
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
    - output_dir: root folder for results (default: <project>/fb-scout-output).
    - save_unverified: also save results Facebook returned that do not contain the keyword.
    - blur_names: blur names and profile pictures of people/pages in the screenshots.
    - show_browser: run in a visible window instead of hidden (headless); use for debugging.
    Requires a prior login (fb_login). Takes roughly 5-15 seconds per saved post.
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
    (fb_label_queue with batch_id) and filter with sentiment='negative'."""
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
                "include_name_matches": include_name_matches, "max_results": max_results,
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
def fb_dataset_stats(keyword: str | None = None, output_dir: str | None = None) -> dict:
    """Overview of the dataset (all runs, duplicates merged): number of distinct posts/comments, how
    many were seen in more than one run, counts by keyword, kind, language, month posted and group, and
    under `analysis` the Phase 3 picture from the primary labels: sentiment, aspects (with sentiment per
    aspect), churn and churn targets, feedback types, sentiment per month and labels per method."""
    return api.dataset_stats(output_dir, keyword)


@mcp.tool()
def fb_dataset_items(
    keyword: str | None = None,
    kind: str | None = None,
    language: str | None = None,
    sentiment: str | None = None,
    aspect: str | None = None,
    churn: str | None = None,
    feedback_type: str | None = None,
    label_method: str | None = None,
    run_id: str | None = None,
    batch_id: str | None = None,
    group: str | None = None,
    since: str | None = None,
    until: str | None = None,
    contains: str | None = None,
    limit: int = 50,
    offset: int = 0,
    full_text: bool = False,
    output_dir: str | None = None,
) -> dict:
    """Distinct posts/comments/listings from the dataset, newest post first, with posted_at, language,
    sentiment label and reason, author, group, price/location (listings), URLs, text (cut to 400
    characters unless full_text) and the absolute screenshot_file path.

    Filters: keyword; kind, language and sentiment as comma-separated lists (e.g. 'post,group_post',
    'ur,ur-Latn', 'negative'); aspect, churn and feedback_type lists (e.g. 'price,warranty',
    'considering,switched', 'complaint'), judged from the primary label (gold > claude-api > agent > model)
    or from label_method's labels ('model', 'human:A', ...); run_id (items of one run) or batch_id (of one
    study); group (name/URL contains); since/until as
    YYYY-MM-DD on the posted date; contains (text, image text, price or location). Page through with
    limit (max 500) and offset."""
    return api.dataset_items(output_dir, limit, offset, full_text, keyword=keyword, kind=kind, language=language,
                             sentiment=sentiment, aspect=aspect, churn=churn, feedback_type=feedback_type,
                             method=label_method, run_id=run_id, batch_id=batch_id, group=group, since=since,
                             until=until, contains=contains)


@mcp.tool()
def fb_label_queue(
    run_id: str | None = None,
    keyword: str | None = None,
    batch_id: str | None = None,
    limit: int = 20,
    output_dir: str | None = None,
) -> dict:
    """Items that still need a sentiment label (for the keyword that found them), with their text, so you
    can read and judge them. Narrow it to one run (run_id from fb_search), a keyword or a study (batch_id).
    `remaining` says how many are left in total. Save your judgements with fb_label_items."""
    return api.label_queue(output_dir, keyword, run_id, batch_id, limit)


@mcp.tool()
def fb_label_items(labels: list[dict], output_dir: str | None = None) -> dict:
    """Save your labels for items from fb_label_queue: a list of
    {"item_id", "sentiment", "reason", "aspects", "churn", "churn_target", "feedback_type", "keyword"?}.
    Everything is judged TOWARDS THE KEYWORD (brand/product/topic), following the rubric:
    - sentiment: negative (complaint, criticism, bad experience, defect, scam/fraud claim, refund or service
      problem, warning others, anger, switching away, sarcastic praise) / positive (praise, recommendation,
      satisfaction) / neutral (ads, sale listings, price lists, announcements, news, questions without a
      complaint, opinions about something else). An edit counts ("Edit: worst experience" is negative).
      Roman Urdu/Urdu cues: bekar, ghatiya, kharab, fraud, dhoka, paisay zaya, masla, شکایت, خراب.
    - aspects: [{"aspect", "sentiment"}] with aspect one of price, product_quality, performance, durability,
      installation, delivery, customer_service, warranty, availability, safety, other; [] when there is no
      opinion (e.g. an ad).
    - churn: none / considering (thinking of leaving, asking for alternatives) / switched (left, won't buy
      again); churn_target: the brand they move to, or "".
    - feedback_type: complaint, defect_report, feature_request, praise, question, advertisement, news, other.
    - reason: one short sentence quoting the deciding words.
    Only sentiment is required (older labels have just that), but give all fields. keyword can be left out
    when the item was found by only one keyword. Stored as method "agent" in the dataset and labels.json."""
    return api.label_items(labels, output_dir)


@mcp.tool()
async def fb_annotate(
    method: Literal["claude-api", "model"],
    keyword: str | None = None,
    run_id: str | None = None,
    batch_id: str | None = None,
    limit: int = 100,
    mode: Literal["sync", "batch"] = "sync",
    model: str | None = None,
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None,
    dry_run: bool = False,
    output_dir: str | None = None,
) -> dict:
    """Label items automatically (Phase 3), as a method to compare with people and with each other:
    - 'claude-api': a fixed Claude model (default claude-opus-5-5, effort medium) with the fixed rubric and a
      JSON schema; all fields (sentiment, aspects, churn, feedback type). Needs the `claude` extra and an
      Anthropic API key; COSTS MONEY (roughly US$0.5-1.5 per 100 items; batch mode is half price but
      returns later). Always call with dry_run=true first and tell the user the estimate before running.
    - 'model': the local multilingual model (XLM-RoBERTa), sentiment only, free, offline; needs the `ml`
      extra (about 1.3 GB, downloaded once).
    Only items this method hasn't labeled yet; narrow with keyword / run_id / batch_id; at most `limit`."""
    return await asyncio.to_thread(api.analyze, method, output_dir, keyword, run_id, batch_id, limit, mode, model,
                                   effort, dry_run)


@mcp.tool()
async def fb_collect_batch(batch_id: str, wait_minutes: float = 0, output_dir: str | None = None) -> dict:
    """Save the labels of a Claude API Message Batch submitted by fb_annotate(mode='batch'). If it isn't
    finished, says so (batches usually take under an hour, at most 24 hours)."""
    return await asyncio.to_thread(api.collect_batch, batch_id, output_dir, wait_minutes)


@mcp.tool()
def fb_gold(
    action: Literal["sample", "import", "adjudicate"],
    n: int = 300,
    keyword: str | None = None,
    stratify: bool = False,
    seed: int = 42,
    file: str | None = None,
    annotator: str | None = None,
    a: str = "A",
    b: str = "B",
    output_dir: str | None = None,
) -> dict:
    """The hand-labeled gold set that methods are evaluated against:
    - 'sample': a blind sheet (CSV for Excel, no AI labels) of `n` random items (stratify=true: equal
      numbers per sentiment) plus an instructions file with the rubric. Two people label it independently.
    - 'import': read a filled sheet (`file`) as `annotator` ("A", "B", or "gold" for final labels).
    - 'adjudicate': a sheet of the items annotators `a` and `b` disagree on; a third person decides and the
      sheet is imported as annotator "gold"."""
    if action == "sample":
        return api.gold_sample(output_dir, n, keyword, seed, stratify, file)
    if action == "import":
        if not file or not annotator:
            return {"ok": False, "error": "invalid_argument", "message": "import needs file and annotator"}
        return api.gold_import(file, annotator, output_dir)
    return api.gold_adjudication(output_dir, a, b, keyword, file)


@mcp.tool()
def fb_evaluate(reference: str = "gold", methods: list[str] | None = None, keyword: str | None = None,
                output_dir: str | None = None) -> dict:
    """Compare labeling methods with the reference: precision, recall, F1 and confusion matrix per class
    for sentiment, churn and feedback type; precision/recall/F1 for aspects; Cohen's kappa; and agreement
    between annotators A and B. reference: 'gold' (the gold annotator, else items where A and B agree), or
    a method ('agent', 'claude-api', 'model', 'human:A') to compare two methods. Saves a Markdown and JSON
    report in <output>/_reports/."""
    return api.evaluate(output_dir, reference, methods, keyword)


@mcp.tool()
def fb_export(
    format: Literal["csv", "jsonl", "parquet"] = "csv",
    anonymize: bool = False,
    keyword: str | None = None,
    kind: str | None = None,
    language: str | None = None,
    sentiment: str | None = None,
    aspect: str | None = None,
    churn: str | None = None,
    feedback_type: str | None = None,
    label_method: str | None = None,
    run_id: str | None = None,
    batch_id: str | None = None,
    group: str | None = None,
    since: str | None = None,
    until: str | None = None,
    contains: str | None = None,
    file: str | None = None,
    output_dir: str | None = None,
) -> dict:
    """Export the dataset (one row per distinct post/comment/listing) for Excel, pandas, R or SPSS, with
    sentiment labels, listing price/location/condition and all metadata.
    csv opens directly in Excel (UTF-8, Urdu works); parquet needs the optional pyarrow extra.
    anonymize=true replaces authors with stable pseudonyms (author_id) and drops all URLs; names inside
    the text and screenshots are not removed. Same filters as fb_dataset_items (e.g. sentiment='negative').
    Default file: <output>/_exports/fbscout_<keyword|all>_<timestamp>.<format>."""
    return api.dataset_export(output_dir, format, file, anonymize, keyword=keyword, kind=kind, language=language,
                              sentiment=sentiment, aspect=aspect, churn=churn, feedback_type=feedback_type,
                              method=label_method, run_id=run_id, batch_id=batch_id, group=group, since=since,
                              until=until, contains=contains)


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
