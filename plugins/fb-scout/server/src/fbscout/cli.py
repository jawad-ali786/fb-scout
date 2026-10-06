"""Command line interface: run FB Scout without any AI agent.

    fbscout status
    fbscout login [--from-firefox | --cookies FILE] [--force] [--timeout 600]
    fbscout search "keyword" [--max 20] [--group URL] [--match phrase|all|any] [--comments] [--blur-names] ...
    fbscout batch study.json [--dry-run]
    fbscout runs [--keyword K]
    fbscout db import | stats [--keyword K] | export [--format csv|jsonl|parquet] [--anonymize] [filters]
    fbscout db exclude ITEM_ID... --reason TEXT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from . import __version__, api
from .batch import Study
from .scraper import SearchOptions


def _add_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--keyword", help="only items found for this keyword")
    p.add_argument("--kind", help="comma-separated kinds, e.g. post,group_post,comment")
    p.add_argument("--language", help="comma-separated language codes, e.g. en,ur,ur-Latn")
    p.add_argument("--group", help="group name or URL contains this text")
    p.add_argument("--since", help="posted on or after this date (YYYY-MM-DD)")
    p.add_argument("--until", help="posted on or before this date (YYYY-MM-DD)")
    p.add_argument("--contains", help="text or image text contains this")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fbscout", description="Facebook keyword search with screenshots + JSON metadata.")
    p.add_argument("--version", action="version", version=f"fbscout {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show login state, browser and output folder")

    lg = sub.add_parser("login", help="log in: normal Chrome window (default), or copy the login from Firefox / a cookie file")
    src = lg.add_mutually_exclusive_group()
    src.add_argument("--from-firefox", action="store_true", help="copy the Facebook login from your Firefox")
    src.add_argument("--cookies", metavar="FILE", help="import a cookies.txt / JSON export from any browser")
    lg.add_argument("--force", action="store_true", help="open the login window even if already logged in")
    lg.add_argument("--timeout", type=int, default=600, help="seconds to wait for the login window (default 600)")

    s = sub.add_parser("search", help="search a keyword and save matches")
    s.add_argument("keyword")
    s.add_argument("--max", type=int, default=20, dest="max_results", help="matches to save (1-100, default 20)")
    s.add_argument("--group", dest="group_url", help="search inside this Facebook group URL")
    s.add_argument("--match", dest="match_mode", choices=["phrase", "all", "any"], default="phrase")
    s.add_argument("--comments", dest="include_comments", action="store_true", help="also capture matching comments (experimental)")
    s.add_argument("--comment-posts", dest="max_comment_posts", type=int, default=5, help="posts to scan for comments (default 5)")
    s.add_argument("--out", dest="output_dir", help="output root folder (default ./fb-scout-output)")
    s.add_argument("--save-unverified", action="store_true", help="also save results without the keyword")
    s.add_argument("--no-highlight", dest="highlight", action="store_false", help="don't highlight the keyword")
    s.add_argument("--blur-names", action="store_true", help="blur names and profile pictures in screenshots")
    s.add_argument("--show-browser", action="store_true", help="show the browser window (default: hidden/headless)")
    s.add_argument("--max-minutes", type=float, default=10, help="time budget for the run (default 10)")

    b = sub.add_parser("batch", help="run a study file: keywords x (global search + groups), one search at a time")
    b.add_argument("study_file", help="JSON study file (see README)")
    b.add_argument("--dry-run", action="store_true", help="only show the planned searches")

    r = sub.add_parser("runs", help="list previous runs")
    r.add_argument("--out", dest="output_dir")
    r.add_argument("--keyword")
    r.add_argument("--limit", type=int, default=20)

    db = sub.add_parser("db", help="the dataset (SQLite) across all runs")
    dbsub = db.add_subparsers(dest="db_cmd", required=True)
    imp = dbsub.add_parser("import", help="import all runs in the output folder (safe to repeat)")
    imp.add_argument("--out", dest="output_dir")
    st = dbsub.add_parser("stats", help="counts by keyword, kind, language, month and group")
    st.add_argument("--out", dest="output_dir")
    st.add_argument("--keyword")
    exc = dbsub.add_parser("exclude", help="leave items out of the dataset (kept in exclusions.json; run folders unchanged)")
    exc.add_argument("item_ids", nargs="+", metavar="ITEM_ID", help="item ids (i_...) from stats/export")
    exc.add_argument("--reason", required=True, help="why, e.g. 'not about the brand'")
    exc.add_argument("--out", dest="output_dir")
    ex = dbsub.add_parser("export", help="export items as CSV (Excel), JSONL or Parquet")
    ex.add_argument("--out", dest="output_dir")
    ex.add_argument("--format", dest="fmt", choices=["csv", "jsonl", "parquet"], default="csv")
    ex.add_argument("--file", dest="out_file", help="output file (default <output>/_exports/...)")
    ex.add_argument("--anonymize", action="store_true", help="replace authors with pseudonyms and drop all URLs")
    _add_filters(ex)
    return p


async def _cli_progress(message: str, done: int, total: int) -> None:
    print(f"  {message}", file=sys.stderr, flush=True)


def _filters(args: argparse.Namespace) -> dict:
    return {k: getattr(args, k) for k in ("keyword", "kind", "language", "group", "since", "until", "contains")}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # keyword/text may be non-Latin
    except (AttributeError, ValueError):
        pass

    if args.cmd == "status":
        result = asyncio.run(api.status())
    elif args.cmd == "login":
        if args.from_firefox:
            method = "firefox"
        elif args.cookies:
            method = "cookie_file"
        else:
            method = "browser"
            print("A normal Chrome window will open. Log in to Facebook there (including 2FA),\n"
                  "wait until you see your feed, then CLOSE the window. Later searches run hidden.", file=sys.stderr)
        result = asyncio.run(api.login(method, args.cookies, args.timeout, args.force))
    elif args.cmd == "search":
        opts = SearchOptions(
            keyword=args.keyword,
            max_results=args.max_results,
            group_url=args.group_url,
            match_mode=args.match_mode,
            include_comments=args.include_comments,
            max_comment_posts=args.max_comment_posts,
            output_dir=args.output_dir,
            save_unverified=args.save_unverified,
            highlight=args.highlight,
            headless=not args.show_browser,
            max_minutes=args.max_minutes,
            blur_names=args.blur_names,
        )
        result = asyncio.run(api.search(opts, _cli_progress))
    elif args.cmd == "batch":
        try:
            study = Study.load(args.study_file)
        except ValueError as exc:
            result = {"ok": False, "error": "invalid_study", "message": str(exc)}
        else:
            if args.dry_run:
                result = {"ok": True, "dry_run": True, **study.describe()}
            else:
                result = asyncio.run(api.batch(study, _cli_progress))
    elif args.cmd == "db":
        if args.db_cmd == "import":
            result = api.dataset_import(args.output_dir)
        elif args.db_cmd == "stats":
            result = api.dataset_stats(args.output_dir, args.keyword)
        elif args.db_cmd == "exclude":
            result = api.dataset_exclude(args.item_ids, args.reason, args.output_dir)
        else:
            result = api.dataset_export(args.output_dir, args.fmt, args.out_file, args.anonymize, **_filters(args))
    else:
        result = api.runs(args.output_dir, args.keyword, args.limit)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
