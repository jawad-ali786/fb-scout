"""The search pipeline: search page → scroll → extract → verify keyword → screenshot → results.json."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable

from playwright.async_api import Page
from playwright.async_api import Error as PlaywrightError

from .browser import Session, check_page, is_logged_in, open_browser
from .capture import capture_element
from .config import default_output_root, pace_factor
from .dates import resolve
from .errors import FBScoutError, NotLoggedIn
from .extract import (
    DOM_SUMMARY_JS, END_OF_RESULTS_JS, FIND_COMMENTS_JS, FIND_POSTS_JS, MORE_COMMENTS_JS,
    RESULTS_READY_JS, SAFE_HTML_JS, Extracted, extract_comment, extract_post, find_handles, find_post_scope,
    group_name_from_title, hover_time_exact, mark_seen, open_comments, show_all_comments,
)
from .lang import detect_language
from .matching import MODES, MatchResult, clean_keyword, match_keyword, terms_for
from .storage import RunWriter, record_id, utc_now_iso
from .urls import build_search_url, group_root

log = logging.getLogger("fbscout")

Progress = Callable[[str, int, int], Awaitable[None]] | None

MAX_RESULTS_CAP = 100
MAX_COMMENTS_PER_POST = 20
MAX_COMMENT_EXPAND_CLICKS = 15

BROWSER_TZ_JS = "() => ({tz: Intl.DateTimeFormat().resolvedOptions().timeZone, offset: -new Date().getTimezoneOffset()})"


@dataclass
class SearchOptions:
    keyword: str
    max_results: int = 20
    group_url: str | None = None
    match_mode: str = "phrase"
    include_comments: bool = False
    max_comment_posts: int = 5
    output_dir: str | None = None
    save_unverified: bool = False
    highlight: bool = True
    headless: bool = True   # login is the only visible step; searches run hidden
    max_minutes: float = 10.0
    blur_names: bool = False   # blur people's/pages' names and profile pictures in screenshots
    batch_id: str | None = None

    def normalized(self) -> "SearchOptions":
        kw = clean_keyword(self.keyword)
        if not kw:
            raise ValueError("keyword is empty")
        if self.match_mode not in MODES:
            raise ValueError(f"match_mode must be one of {MODES}")
        if self.group_url and not group_root(self.group_url):
            raise ValueError(f"group_url is not a Facebook group URL: {self.group_url!r}")
        return SearchOptions(
            keyword=kw,
            max_results=max(1, min(MAX_RESULTS_CAP, int(self.max_results))),
            group_url=group_root(self.group_url) if self.group_url else None,
            match_mode=self.match_mode,
            include_comments=bool(self.include_comments),
            max_comment_posts=max(0, min(20, int(self.max_comment_posts))),
            output_dir=self.output_dir,
            save_unverified=bool(self.save_unverified),
            highlight=bool(self.highlight),
            headless=bool(self.headless),
            max_minutes=max(1.0, min(60.0, float(self.max_minutes))),
            blur_names=bool(self.blur_names),
            batch_id=self.batch_id,
        )


async def pause(lo: float, hi: float) -> None:
    f = pace_factor()
    if f > 0:
        await asyncio.sleep(random.uniform(lo, hi) * f)


def _text_key(text: str) -> str:
    return "text:" + hashlib.sha1(" ".join(text.split()).lower().encode("utf-8")).hexdigest()


def _verify(opts: SearchOptions, data: Extracted) -> tuple[MatchResult, str | None]:
    """Check the post text first, then everything visible in the container (link previews etc.),
    then the text Facebook read from the post's images."""
    m = match_keyword(opts.keyword, data.text, opts.match_mode)
    if m.ok:
        return m, "text"
    m_full = match_keyword(opts.keyword, data.full_text, opts.match_mode)
    if m_full.ok:
        return m_full, "full_text"
    m_img = match_keyword(opts.keyword, data.image_text, opts.match_mode)
    if m_img.ok:
        return m_img, "image_text"
    return m, None


def _record(*, rid: str, opts: SearchOptions, data: Extracted, match: MatchResult, matched_in: str | None,
            kind: str, source: str, rank: int, shot_name: str | None, post_url: str | None, run: RunWriter,
            parent_post_url: str | None = None, group_name: str | None = None, group_url: str | None = None) -> dict:
    captured_at = utc_now_iso()
    posted = resolve(data.time_exact, data.time_text, captured_at, run.meta.get("browser_utc_offset_minutes"))
    return {
        "id": rid,
        "kind": kind,
        "keyword": opts.keyword,
        "keyword_verified": match.ok,
        "matched_in": matched_in,
        "matched_terms": match.matched_terms,
        "match_snippet": match.snippet,
        "post_url": post_url,
        "comment_url": data.comment_url,
        "parent_post_url": parent_post_url,
        "author_name": data.author_name,
        "author_url": data.author_url,
        "group_name": group_name if group_name is not None else data.group_name,
        "group_url": group_url if group_url is not None else data.group_url,
        "time_text": data.time_text,
        "time_exact": data.time_exact,
        **posted,
        "text": data.text,
        "image_text": data.image_text,
        "language": detect_language(data.text),
        "screenshot_name": shot_name,
        "screenshot_path": f"screenshots/{shot_name}" if shot_name else None,
        "source": source,
        "search_rank": rank,
        "captured_at": captured_at,
    }


async def _screenshot(page: Page, el, run: RunWriter, rank: int, kind: str, rid: str, terms: list[str], opts: SearchOptions) -> str | None:
    name, path = run.screenshot_target(rank, kind, rid)
    try:
        await capture_element(page, el, path, terms, opts.highlight, opts.blur_names)
    except PlaywrightError as exc:
        run.stats["errors"] += 1
        run.warn(f"Screenshot failed for result #{rank}: {str(exc).splitlines()[0][:160]}")
        return None
    run.stats["screenshots"] += 1
    return name


async def wait_for_results(page: Page, run: RunWriter) -> None:
    try:
        await page.wait_for_function(RESULTS_READY_JS, timeout=25000)
    except PlaywrightError:
        run.warn("Search results did not appear within 25s.")
    await pause(1.5, 2.5)


async def collect_posts(page: Page, opts: SearchOptions, run: RunWriter, deadline: float, progress: Progress = None) -> list[dict]:
    """Process the posts on the current (already loaded) search/feed page."""
    terms = terms_for(opts.keyword, opts.match_mode)
    source = "search:group" if opts.group_url else "search:posts"
    start_url = page.url
    saved: list[dict] = []
    seen: set[str] = set()
    rank = 0
    idle_rounds = 0
    max_rounds = min(80, max(10, opts.max_results * 3))
    see_more = True

    for _ in range(max_rounds):
        if time.monotonic() > deadline:
            run.warn(f"Time budget of {opts.max_minutes:g} min reached.")
            break
        new_this_round = 0
        for el in await find_handles(page, FIND_POSTS_JS):
            if len(saved) >= opts.max_results or time.monotonic() > deadline:
                break
            if not await mark_seen(el):
                continue
            try:
                data = await extract_post(page, el, see_more=see_more)
            except PlaywrightError as exc:
                run.stats["errors"] += 1
                log.warning("extract failed: %s", exc)
                continue
            if page.url != start_url:  # "See more" navigated away: go back, stop clicking it
                see_more = False
                run.warn("'See more' opened the post page, so expanding is disabled; long posts may be cut off.")
                await page.go_back(wait_until="domcontentloaded")
                await check_page(page)
                await pause(1.5, 2.5)
                break

            if not data.is_post:   # a person/page card (e.g. matching group members), not a post
                run.stats["skipped_not_posts"] = run.stats.get("skipped_not_posts", 0) + 1
                continue
            key = data.post_url or _text_key(data.text)
            if key in seen:
                continue
            seen.add(key)
            rank += 1
            new_this_round += 1
            run.stats["candidates_seen"] += 1

            match, matched_in = _verify(opts, data)
            if match.ok:
                run.stats["verified"] += 1
            elif not opts.save_unverified:
                continue

            rid = record_id(key)
            kind = "group_post" if opts.group_url and data.kind in ("post", "unknown") else data.kind
            shot = await _screenshot(page, el, run, rank, kind, rid, terms, opts)
            if not data.post_url:
                run.warn("Some records have no post_url (Facebook did not expose a permalink).")
            record = _record(rid=rid, opts=opts, data=data, match=match, matched_in=matched_in, kind=kind,
                             source=source, rank=rank, shot_name=shot, post_url=data.post_url, run=run,
                             group_name=data.group_name or run.meta.get("group_name"),
                             group_url=data.group_url or opts.group_url)
            run.add(record)
            saved.append(record)
            if progress:
                await progress(f"saved {len(saved)}/{opts.max_results}", len(saved), opts.max_results)
            await pause(0.4, 1.1)

        if len(saved) >= opts.max_results:
            break
        idle_rounds = idle_rounds + 1 if new_this_round == 0 else 0
        if idle_rounds >= 4 or (idle_rounds >= 2 and await page.evaluate(END_OF_RESULTS_JS)):
            break
        await page.mouse.move(640, 450)
        await page.mouse.wheel(0, random.randint(1400, 2200))
        await pause(1.8, 3.6)
        await check_page(page)
    return saved


async def collect_comments_on_page(page: Page, parent: dict, opts: SearchOptions, run: RunWriter,
                                   seen: set[str], progress: Progress = None, deadline: float | None = None) -> int:
    """Capture comments containing the keyword on the current (already loaded) post page."""
    terms = terms_for(opts.keyword, opts.match_mode)
    run.stats["comment_posts_scanned"] = run.stats.get("comment_posts_scanned", 0) + 1
    # The post may sit in a dialog over the home feed: never touch the feed's comments.
    if not await find_post_scope(page, parent.get("text")):
        run.warn("Some posts could not be found on their own page, so their comments were skipped.")
        return 0
    if await open_comments(page) == 0:
        return 0   # no comments on this post
    if not await show_all_comments(page):
        run.warn("On some posts the comment order could not be switched to 'All comments'; "
                 "only the comments Facebook shows by default were scanned there.")
    for _ in range(MAX_COMMENT_EXPAND_CLICKS):
        if deadline is not None and time.monotonic() > deadline:
            break
        try:
            clicked = await page.evaluate(MORE_COMMENTS_JS)
        except PlaywrightError:
            clicked = False
        if not clicked:
            break
        await pause(1.2, 2.4)

    count = 0
    for el in await find_handles(page, FIND_COMMENTS_JS):
        if count >= MAX_COMMENTS_PER_POST:
            break
        if not await mark_seen(el):
            continue
        try:
            data = await extract_comment(page, el)
        except PlaywrightError:
            run.stats["errors"] += 1
            continue
        match = match_keyword(opts.keyword, data.text, opts.match_mode)
        if not match.ok:
            continue
        key = data.comment_url or (str(parent.get("post_url")) + "|" + _text_key(data.text))
        if key in seen:
            continue
        seen.add(key)
        data.time_exact = await hover_time_exact(page, el, data.time_link_index)
        rid = record_id(key)
        rank = run.stats["saved"] + 1
        shot = await _screenshot(page, el, run, rank, data.kind, rid, terms, opts)
        record = _record(rid=rid, opts=opts, data=data, match=match, matched_in="text", kind=data.kind,
                         source="comments", rank=rank, shot_name=shot, post_url=parent.get("post_url"), run=run,
                         parent_post_url=parent.get("post_url"),
                         group_name=parent.get("group_name"), group_url=parent.get("group_url"))
        run.add(record)
        run.stats["comments_saved"] += 1
        count += 1
        if progress:
            await progress(f"comments saved {run.stats['comments_saved']}", run.stats["saved"], 0)
        await pause(0.3, 0.8)
    return count


async def collect_comments(session: Session, posts: list[dict], opts: SearchOptions, run: RunWriter,
                           deadline: float, progress: Progress = None) -> None:
    targets = [p for p in posts if p.get("post_url")][: opts.max_comment_posts]
    if not targets:
        run.warn("No post URLs available to scan comments.")
        return
    page = await session.context.new_page()
    seen: set[str] = set()
    try:
        for post in targets:
            if time.monotonic() > deadline:
                run.warn("Time budget reached during comment scan.")
                break
            await page.goto(post["post_url"], wait_until="domcontentloaded", timeout=45000)
            await check_page(page)
            await pause(2.5, 4.0)
            await collect_comments_on_page(page, post, opts, run, seen, progress, deadline)
            await pause(2.0, 4.0)
    finally:
        await page.close()


async def dump_debug(page: Page, run: RunWriter, reason: str) -> None:
    """Save what the page looked like, to fix selectors. Scripts (session tokens) are stripped."""
    d = run.debug_dir()
    try:
        await page.screenshot(path=str(d / f"{reason}_viewport.png"))
        (d / f"{reason}_dom_summary.json").write_text(
            json.dumps(await page.evaluate(DOM_SUMMARY_JS), ensure_ascii=False, indent=2), encoding="utf-8")
        (d / f"{reason}_page.html").write_text(await page.evaluate(SAFE_HTML_JS), encoding="utf-8")
        run.warn(f"Debug files saved in {d} ({reason}).")
    except (PlaywrightError, OSError) as exc:
        log.warning("debug dump failed: %s", exc)


async def run_search(opts: SearchOptions, progress: Progress = None) -> dict:
    """Full pipeline. Returns the run summary (or an error dict if nothing could start)."""
    opts = opts.normalized()
    output_root = Path(opts.output_dir) if opts.output_dir else default_output_root()
    deadline = time.monotonic() + opts.max_minutes * 60

    try:
        async with open_browser(headless=opts.headless) as session:
            if not await is_logged_in(session.context):
                raise NotLoggedIn()
            params = {k: v for k, v in asdict(opts).items() if k not in ("keyword", "output_dir")}
            params["scope"] = "search:group" if opts.group_url else "search:posts"
            params["browser"] = session.channel
            run = RunWriter(output_root, opts.keyword, params)
            page = await session.page()
            status, error = "cancelled", None
            try:
                tz = await page.evaluate(BROWSER_TZ_JS)  # Facebook shows times in this timezone
                run.meta["browser_timezone"] = tz.get("tz")
                run.meta["browser_utc_offset_minutes"] = tz.get("offset")
            except PlaywrightError:
                pass
            try:
                url = build_search_url(opts.keyword, opts.group_url)
                run.meta["search_url"] = url
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await check_page(page)
                await wait_for_results(page, run)
                if opts.group_url:  # results inside a group don't repeat the group's name
                    run.meta["group_name"] = group_name_from_title(await page.title())
                posts = await collect_posts(page, opts, run, deadline, progress)
                if run.stats["candidates_seen"] == 0:
                    run.warn("No posts were found on the results page.")
                    await dump_debug(page, run, "no_candidates")
                if opts.include_comments and posts:
                    await collect_comments(session, posts, opts, run, deadline, progress)
                status = "completed"
            except FBScoutError as exc:
                status, error = "stopped", {"code": exc.code, "message": str(exc), "hint": exc.hint}
                await dump_debug(page, run, exc.code)
            except Exception as exc:  # keep partial results on any failure
                log.exception("run failed")
                status, error = "failed", {"code": "unexpected", "message": repr(exc)[:500], "hint": None}
                if not page.is_closed():
                    await dump_debug(page, run, "unexpected")
            finally:
                run.finish(status, error)
            return run.summary()
    except FBScoutError as exc:
        return exc.to_dict()
