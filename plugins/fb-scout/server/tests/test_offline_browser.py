"""End-to-end extraction on local fake-Facebook pages (no network, no login).

Uses the installed Chrome/Edge in headless mode; skipped if no browser is available.
"""

import asyncio
import json
import time
from pathlib import Path

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from fbscout.capture import capture_element, highlight, mark_names
from fbscout.extract import FIND_POSTS_JS, find_handles
from fbscout.scraper import SearchOptions, collect_comments_on_page, collect_posts, wait_for_results
from fbscout.storage import RunWriter

FIXTURES = Path(__file__).parent / "fixtures"
PNG_MAGIC = b"\x89PNG"


@pytest.fixture(autouse=True)
def no_delays(monkeypatch):
    monkeypatch.setenv("FBSCOUT_PACE", "0")


def run_on_page(fixture: str, body):
    async def main():
        async with async_playwright() as pw:
            browser = None
            for channel in ("chrome", "msedge", None):
                try:
                    browser = await pw.chromium.launch(channel=channel, headless=True)
                    break
                except PlaywrightError:
                    continue
            if browser is None:
                pytest.skip("no Chromium-based browser available")
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 900})
                await page.goto((FIXTURES / fixture).as_uri())
                return await body(page)
            finally:
                await browser.close()
    return asyncio.run(main())


def test_collect_posts(tmp_path):
    opts = SearchOptions(keyword="solar panel", max_results=10, include_types="all").normalized()

    async def body(page):
        run = RunWriter(tmp_path, opts.keyword, {"scope": "search:posts"})
        await wait_for_results(page, run)
        records = await collect_posts(page, opts, run, deadline=time.monotonic() + 120)
        run.finish("completed")
        return run, records

    run, records = run_on_page("search.html", body)

    assert run.stats["candidates_seen"] == 10     # 11 posts, one duplicate
    assert run.stats["skipped_not_posts"] == 1    # the member card
    assert run.stats["verified"] == 7             # not: keyword only in the group name / a comment preview
    assert run.stats["saved"] == 7
    assert run.stats["screenshots"] == 7

    group_post, page_post, hashtag_reel, video_post, group_photo_post, poster_post, sale_post = records
    assert group_post["kind"] == "group_post"
    assert group_post["post_url"] == "https://www.facebook.com/groups/123456/posts/789012/"   # revealed by hover, tracking removed
    assert group_post["author_name"] == "Ali Khan"
    assert group_post["author_url"] == "https://www.facebook.com/groups/123456/user/1000123/"
    assert group_post["group_name"] == "Solar Users Group"
    assert group_post["group_url"] == "https://www.facebook.com/groups/123456/"
    assert group_post["time_text"] == "3d"
    assert group_post["time_exact"] == "Sunday, September 28, 2026 at 4:12 PM"
    assert "Budget is 500k" in group_post["text"]                  # "See more" was expanded
    assert "See less" not in group_post["text"]
    assert "comment preview" not in group_post["text"]
    assert group_post["matched_in"] == "text"
    assert group_post["image_text"] == "May be an image of text"
    assert group_post["posted_at"].startswith("2026-09-28T16:12:00")    # from the tooltip
    assert group_post["posted_at_precision"] == "minute"
    assert group_post["language"] == "en"

    assert hashtag_reel["kind"] == "reel" and hashtag_reel["matched_in"] == "text"   # "#solarpanel"
    assert hashtag_reel["post_url"] == "https://www.facebook.com/reel/987654321/"

    assert page_post["kind"] == "post"
    assert page_post["post_url"] == "https://www.facebook.com/SolarCo/posts/pfbid02abc/"
    assert page_post["author_name"] == "SolarCo"
    assert page_post["time_text"] == "1h"
    assert page_post["time_exact"] is None                          # not the group post's lingering tooltip
    assert "Solar Panel" in page_post["match_snippet"]

    assert video_post["kind"] == "reel"                             # only link is the hover-revealed timestamp
    assert video_post["post_url"] == "https://www.facebook.com/reel/111222/"
    assert video_post["author_name"] == "Kamal Solar"               # not the profile picture's label
    assert video_post["time_text"] is None                          # timestamp text is not readable
    assert video_post["time_exact"] == "Monday 10 August 2026 at 14:15"

    assert group_photo_post["kind"] == "group_post"                 # derived from the photo link's set=pcb.<id>
    assert group_photo_post["post_url"] == "https://www.facebook.com/groups/123456/posts/555666/"
    assert group_photo_post["author_name"] == "Sana Traders"
    assert group_photo_post["group_name"] == "Solar Users Group"
    assert group_photo_post["time_text"] is None                    # not the image description
    assert group_photo_post["time_exact"] is None                   # not the video post's lingering tooltip
    assert group_photo_post["text"] == "Selling solar panel stock in Karachi, wholesale rates"   # filler dropped

    assert poster_post["matched_in"] == "image_text"                # keyword only in the poster
    assert poster_post["image_text"] == "May be an image of text that says 'SOLAR PANEL SALE 20% OFF'"
    assert "SOLAR PANEL" in poster_post["match_snippet"]
    assert poster_post["time_text"] == "2d"
    assert poster_post["posted_at_precision"] == "day"              # relative to captured_at

    assert sale_post["matched_in"] == "full_text"                   # the sale listing's title
    assert "24v solar panel kit" in sale_post["match_snippet"]
    assert sale_post["post_url"] == "https://www.facebook.com/groups/999/posts/1234/"

    assert group_post["content_type"] is None                       # someone asking: an ordinary post
    assert group_photo_post["content_type"] == "promotion" and "selling" in group_photo_post["content_reason"]
    assert sale_post["content_type"] == "promotion"

    for rec in records:
        shot = run.dir / rec["screenshot_path"]
        assert shot.read_bytes()[:4] == PNG_MAGIC

    saved = json.loads((run.dir / "results.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in saved["results"]] == [r["id"] for r in records]
    assert saved["run"]["status"] == "completed"


def test_promotions_are_left_out_by_default(tmp_path):
    opts = SearchOptions(keyword="solar panel", max_results=10).normalized()

    async def body(page):
        run = RunWriter(tmp_path, opts.keyword, {"scope": "search:posts"})
        records = await collect_posts(page, opts, run, deadline=time.monotonic() + 120)
        run.finish("completed")
        return run, records

    run, records = run_on_page("search.html", body)
    left_out = run.stats.get("skipped_promotion", 0)
    assert left_out >= 2 and run.stats["verified"] == 7
    assert run.stats["saved"] == run.stats["screenshots"] == 7 - left_out     # no screenshot of what was left out
    assert all(r["content_type"] is None for r in records)
    assert any("Budget is 500k" in r["text"] for r in records)               # the question is kept
    assert not any(r["text"].startswith(("Selling solar panel", "FOR SALE")) for r in records)
    saved = json.loads((run.dir / "results.json").read_text(encoding="utf-8"))["run"]
    assert len(saved["filtered_examples"]) == left_out
    assert all(e["content_type"] == "promotion" and e["reason"] for e in saved["filtered_examples"])
    assert run.summary()["filtered_out"] == {"promotion": left_out}


def test_save_unverified_keeps_fuzzy_results(tmp_path):
    opts = SearchOptions(keyword="solar panel", max_results=10, save_unverified=True, include_types="all").normalized()

    async def body(page):
        run = RunWriter(tmp_path, opts.keyword, {})
        return await collect_posts(page, opts, run, deadline=time.monotonic() + 120)

    records = run_on_page("search.html", body)
    assert len(records) == 10                                       # never the member card
    assert [r["keyword_verified"] for r in records] == [True, True, False, True, True, True, True, False, False, True]
    assert "All type solar panel" not in (records[7]["match_snippet"] or "")
    assert records[3]["kind"] == "reel"


def test_max_results_stops_early(tmp_path):
    opts = SearchOptions(keyword="solar panel", max_results=1).normalized()

    async def body(page):
        run = RunWriter(tmp_path, opts.keyword, {})
        return await collect_posts(page, opts, run, deadline=time.monotonic() + 120)

    assert len(run_on_page("search.html", body)) == 1


def test_highlight_marks_keyword():
    async def body(page):
        first = (await find_handles(page, FIND_POSTS_JS))[0]
        return await highlight(first, ["solar panel"])

    assert run_on_page("search.html", body) >= 1


def test_blur_names_marks_only_profile_links_and_cleans_up(tmp_path):
    async def body(page):
        first = (await find_handles(page, FIND_POSTS_JS))[0]
        marked = await mark_names(first)
        blurred = await first.evaluate("el => Array.from(el.querySelectorAll('[data-fbscout-blur]')).map(a => a.innerText)")
        await first.evaluate("el => el.querySelectorAll('[data-fbscout-blur]').forEach(n => n.removeAttribute('data-fbscout-blur'))")
        await capture_element(page, first, tmp_path / "shot.png", ["solar panel"], blur_names=True)
        left = await page.evaluate("() => document.querySelectorAll('[data-fbscout-blur]').length")
        return marked, blurred, left

    marked, blurred, left = run_on_page("search.html", body)
    assert marked == 1 and blurred == ["Ali Khan"]       # the author, not the group or the photo
    assert left == 0                                     # attributes removed after the screenshot
    assert (tmp_path / "shot.png").read_bytes()[:4] == PNG_MAGIC


def test_collect_comments(tmp_path):
    opts = SearchOptions(keyword="solar panel", include_comments=True, include_types="all").normalized()
    parent = {
        "post_url": "https://www.facebook.com/groups/123456/posts/789012/",
        "group_name": "Solar Users Group",
        "group_url": "https://www.facebook.com/groups/123456/",
        "text": "Which brand of inverter do you use?",
    }

    async def body(page):
        run = RunWriter(tmp_path, opts.keyword, {})
        n = await collect_comments_on_page(page, parent, opts, run, set())
        decoy_opened = await page.evaluate("() => document.getElementById('decoycomments').children.length")
        return run, n, decoy_opened

    run, n, decoy_opened = run_on_page("post.html", body)
    assert decoy_opened == 0                           # the feed post behind the dialog was never touched
    assert n == 3 and run.stats["comments_saved"] == 3
    assert run.stats["comment_posts_scanned"] == 1
    assert not any("All comments" in w for w in run.meta["warnings"])   # the sort was switched
    comment, all_only, reply = run.results

    assert comment["kind"] == "comment"
    assert comment["comment_url"] == "https://www.facebook.com/groups/123456/posts/789012/?comment_id=1111"
    assert comment["author_name"] == "Sara Ahmed"
    assert comment["author_url"] == "https://www.facebook.com/profile.php?id=100200"
    assert comment["text"] == "My solar panel stopped working after a month, very bad service."
    assert comment["parent_post_url"] == parent["post_url"]
    assert comment["group_name"] == "Solar Users Group"
    assert comment["source"] == "comments"
    assert comment["time_text"] == "2h"
    assert comment["time_exact"] == "Friday, October 2, 2026 at 9:15 AM"      # hover tooltip
    assert comment["posted_at"].startswith("2026-10-02T09:15:00")

    assert all_only["author_name"] == "Zain"                # only listed under "All comments"
    assert all_only["content_type"] == "promotion" and comment["content_type"] is None
    assert all_only["time_exact"] is None                   # not Sara's lingering tooltip
    assert all_only["posted_at_precision"] == "hour"        # from "5h"

    assert reply["kind"] == "reply"                         # revealed by "View 1 more reply"
    assert reply["comment_url"].endswith("comment_id=3333&reply_comment_id=4444")
    assert reply["author_name"] == "Kamran"
    for rec in run.results:
        assert (run.dir / rec["screenshot_path"]).read_bytes()[:4] == PNG_MAGIC


def test_comment_ads_are_left_out(tmp_path):
    opts = SearchOptions(keyword="solar panel", include_comments=True).normalized()

    async def body(page):
        run = RunWriter(tmp_path, opts.keyword, {})
        n = await collect_comments_on_page(page, {"post_url": "https://www.facebook.com/groups/123456/posts/789012/",
                                                  "text": "Which brand of inverter do you use?"}, opts, run, set())
        return run, n

    run, n = run_on_page("post.html", body)
    assert n == 2 and run.stats["skipped_promotion"] == 1    # "Cheap solar panel deals, DM me"
    assert [r["author_name"] for r in run.results] == ["Sara Ahmed", "Kamran"]


def test_post_without_comments_is_skipped_quietly(tmp_path):
    opts = SearchOptions(keyword="solar panel", include_comments=True).normalized()

    async def body(page):
        await page.set_content('<div role="main"><div role="article"><div dir="auto">solar panel for sale</div>'
                               '<div role="button" aria-label="Leave a comment"></div></div></div>')
        run = RunWriter(tmp_path, opts.keyword, {})
        n = await collect_comments_on_page(page, {"post_url": None}, opts, run, set())
        return run, n

    run, n = run_on_page("post.html", body)
    assert n == 0 and run.stats["comment_posts_scanned"] == 1
    assert run.meta["warnings"] == []                # no "could not switch to All comments" noise


def test_comments_skipped_when_post_is_not_on_its_page(tmp_path):
    opts = SearchOptions(keyword="solar panel", include_comments=True).normalized()

    async def body(page):
        run = RunWriter(tmp_path, opts.keyword, {})
        n = await collect_comments_on_page(page, {"post_url": "x", "text": "A post that is not on this page"},
                                           opts, run, set())
        decoy_opened = await page.evaluate("() => document.getElementById('decoycomments').children.length")
        return run, n, decoy_opened

    run, n, decoy_opened = run_on_page("post.html", body)
    assert n == 0 and decoy_opened == 0
    assert any("could not be found on their own page" in w for w in run.meta["warnings"])


@pytest.mark.parametrize("html, error", [
    ('<div role="dialog">You\'re Temporarily Blocked. It looks like you were misusing this feature.</div>', "blocked"),
    ('<div role="dialog">You can’t use this feature right now</div>', "blocked"),
    ('<div>Your account has been locked. Confirm your identity to continue.</div>', "checkpoint"),
    # A post that merely talks about being blocked must not stop the run.
    ('<div role="feed"><div role="article">Facebook says you\'re temporarily blocked? Same here lol</div></div>', None),
])
def test_check_page_stops_on_block_and_checkpoint_notices(html, error):
    from fbscout.browser import check_page
    from fbscout.errors import FBScoutError

    async def body(page):
        await page.set_content(html)
        try:
            await check_page(page)
        except FBScoutError as exc:
            return exc.code
        return None

    assert run_on_page("post.html", body) == error


def test_partial_results_survive_a_stop(tmp_path):
    """results.json is rewritten after every record, so a run stopped by a checkpoint keeps what it had."""
    run = RunWriter(tmp_path, "solar panel", {"scope": "search:posts"})
    run.add({"id": "r_1", "kind": "post", "text": "solar panel"})
    on_disk = json.loads((run.dir / "results.json").read_text(encoding="utf-8"))
    assert on_disk["run"]["status"] == "running" and len(on_disk["results"]) == 1
    run.finish("stopped", {"code": "checkpoint", "message": "Facebook is showing a security checkpoint.", "hint": None})
    on_disk = json.loads((run.dir / "results.json").read_text(encoding="utf-8"))
    assert on_disk["run"]["status"] == "stopped" and on_disk["run"]["error"]["code"] == "checkpoint"
    assert len(on_disk["results"]) == 1


def test_highlight_marks_words_joined_by_special_characters():
    async def body(page):
        await page.set_content('<div id="p">LONGi 645W Solar+Panel, a solar-panel kit, SOLAR PANEL sale, '
                               '#solarpanel, solar_panel, solar🌞panel, سولر پینل</div>')
        el = await page.query_selector("#p")
        n = await highlight(el, ["solar panel"])
        marked = await page.evaluate("() => Array.from(CSS.highlights.get('fbscout')).map(r => r.toString())")
        urdu = await highlight(el, ["سولر-پینل"])
        return n, marked, urdu

    n, marked, urdu = run_on_page("post.html", body)
    assert marked == ["Solar+Panel", "solar-panel", "SOLAR PANEL", "solarpanel", "solar_panel", "solar🌞panel"] and n == 6
    assert urdu == 1                                   # Urdu letters are letters, not separators


def test_include_name_matches_keeps_name_only_posts_and_profile_cards(tmp_path):
    opts = SearchOptions(keyword="solar panel", max_results=20, include_name_matches=True,
                         include_types="all").normalized()

    async def body(page):
        run = RunWriter(tmp_path, opts.keyword, {})
        return run, await collect_posts(page, opts, run, deadline=time.monotonic() + 120)

    run, records = run_on_page("search.html", body)
    assert run.stats["skipped_not_posts"] == 0
    by_text = {r["text"][:20]: r for r in records}
    group_name_only = by_text["FOR SALE: lithium ba"]
    assert group_name_only["matched_in"] == "name" and group_name_only["kind"] == "group_post"
    card = next(r for r in records if r["kind"] == "profile")
    assert card["matched_in"] == "profile" and card["post_url"] == "https://www.facebook.com/solarpanel.ali/"
    assert card["author_name"] == "Ali (Solar Panel expert)"
    assert not any("hybrid inverter range" in r["text"] for r in records)   # comment preview still doesn't count
    assert len(records) == 9
