"""Marketplace: card/page parsing (pure) and collection on local fake Marketplace pages."""

import time
from pathlib import Path

import pytest

from fbscout.extract import parse_listing_card, parse_listing_page
from fbscout.scraper import SearchOptions, collect_listings, fill_listing_details, wait_for_results
from fbscout.extract import LISTINGS_READY_JS
from fbscout.storage import RunWriter
from fbscout.urls import build_marketplace_url

from test_offline_browser import PNG_MAGIC, run_on_page

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_delays(monkeypatch):
    monkeypatch.setenv("FBSCOUT_PACE", "0")


def test_build_marketplace_url():
    assert build_marketplace_url("solar panel") == "https://www.facebook.com/marketplace/search/?query=solar%20panel"
    assert build_marketplace_url("solar panel", "karachi") == \
        "https://www.facebook.com/marketplace/karachi/search/?query=solar%20panel"
    assert build_marketplace_url("x", "/110713778953693/").startswith("https://www.facebook.com/marketplace/110713778953693/")


def test_parse_listing_card():
    card = {"href": "https://www.facebook.com/marketplace/item/1111/?ref=search&tracking=browse_serp%3A4000d2",
            "text": "PKR8,000\nSolar panel 585 watt Longi bullet damaged\nKarachi, Pakistan",
            "alt": "Solar panel 585 watt Longi\nbullet damaged in Karachi, Pakistan"}
    listing = parse_listing_card(card)
    assert listing.url == "https://www.facebook.com/marketplace/item/1111/"      # tracking removed
    assert listing.price == "PKR8,000" and listing.location == "Karachi, Pakistan"
    assert listing.title == "Solar panel 585 watt Longi\nbullet damaged"           # line break kept from the alt
    free = parse_listing_card({"href": "x", "text": "FREE\nOld inverter\nLahore, Pakistan", "alt": ""})
    assert free.price == "FREE" and free.title == "Old inverter"


def test_parse_listing_page_ignores_other_listings():
    text = ("Solar panel 585 watt\nPKR8,000\nListed 2 days ago in Karachi, Pakistan\nMessage\nDetails\nCondition\n"
            "Used – good\nWorking 100%\nCall if interested\nKarachi, Pakistan · Location is approximate\n"
            "Seller information\nSeller details\nBilal Ahmed\nJoined Facebook in 2016\nRelated searches\n"
            "solar panel 400 watts\nToday's picks\nPKR650\nT9 Trimmer Machine")
    out = parse_listing_page({"title": "Solar panel 585 watt", "text": text, "sellers": [
        {"href": "https://www.facebook.com/marketplace/profile/100000000000003/?product_id=1", "text": "Seller details"},
        {"href": "https://www.facebook.com/marketplace/profile/100000000000003/?product_id=1", "text": "Bilal Ahmed"}]})
    assert out["listed_text"] == "2 days ago" and out["location"] == "Karachi, Pakistan"
    assert out["condition"] == "Used – good"
    assert out["description"] == "Working 100%\nCall if interested"
    assert out["seller_name"] == "Bilal Ahmed"
    assert out["seller_url"] == "https://www.facebook.com/marketplace/profile/100000000000003/"


def test_collect_listings(tmp_path):
    opts = SearchOptions(keyword="solar panel", source="marketplace", max_results=10).normalized()

    async def body(page):
        run = RunWriter(tmp_path, opts.keyword, {"scope": opts.scope})
        await wait_for_results(page, run, LISTINGS_READY_JS)
        records = await collect_listings(page, opts, run, deadline=time.monotonic() + 120)
        return run, records

    run, records = run_on_page("marketplace.html", body)
    assert run.stats["candidates_seen"] == 3          # 4 cards, one repeated
    assert run.stats["verified"] == 2                 # not "Solar structure"
    first, second = records
    assert first["kind"] == "marketplace" and first["source"] == "search:marketplace"
    assert first["post_url"] == "https://www.facebook.com/marketplace/item/1111/"
    assert first["price"] == "PKR8,000" and first["location"] == "Karachi, Pakistan"
    assert first["text"] == "Solar panel 585 watt Longi\nbullet damaged"
    assert first["matched_in"] == "title"
    assert second["price"] == "FREE" and second["text"] == "Used solar-panel set"   # "solar-panel" counts
    assert all(r["content_type"] == "marketplace" for r in records)   # kept: the user asked for listings
    for rec in records:
        assert (run.dir / rec["screenshot_path"]).read_bytes()[:4] == PNG_MAGIC


def test_listing_details(tmp_path):
    opts = SearchOptions(keyword="solar panel", source="marketplace").normalized()
    listing_url = (FIXTURES / "listing.html").as_uri()

    async def body(page):
        run = RunWriter(tmp_path, opts.keyword, {"scope": opts.scope, "browser_utc_offset_minutes": 300})
        rec = {"id": "r_1", "kind": "marketplace", "post_url": listing_url, "text": "Solar panel 585 watt Longi",
               "price": "PKR8,000", "location": "Karachi, Pakistan", "captured_at": "2026-10-06T08:00:00Z"}
        run.add(rec)
        await fill_listing_details(page, [rec], run, deadline=time.monotonic() + 120)
        return run, rec

    run, rec = run_on_page("listing.html", body)
    assert rec["text"].startswith("Solar panel 585 watt Longi\n\nWorking 100% check krwa kr denge")
    assert "Bilkul bekar nahi" in rec["text"]                  # "See more" was expanded
    assert "Trimmer" not in rec["text"]                        # "Today's picks" is not the listing
    assert rec["condition"] == "Used – good"
    assert rec["author_name"] == "Imran Solar Traders"
    assert rec["author_url"] == "https://www.facebook.com/marketplace/profile/100000000000002/"
    assert rec["time_text"] == "over a week ago" and rec["posted_at_precision"] == "week"
    assert rec["location"] == "Karachi, Pakistan" and rec["language"] == "ur-Latn"
    assert run.stats["listings_opened"] == 1


def test_discounted_card_and_place_on_next_line():
    card = parse_listing_card({"href": "x", "text": "PKR8,100\nPKR10,000\n10panel 1plate price 10k\nIslamabad, Pakistan",
                               "alt": ""})
    assert card.price == "PKR8,100" and card.title == "10panel 1plate price 10k"   # old price not in the title
    page = parse_listing_page({"title": "t", "sellers": [], "text": (
        "t\nPKR1\nListed 2 weeks ago in\nIslamabad, Pakistan\nDetails\nCondition\nUsed\nGood panel\n"
        "See translation\nSeller information")})
    assert page["listed_text"] == "2 weeks ago" and page["location"] == "Islamabad, Pakistan"
    assert page["description"] == "Good panel"                                    # no "See translation"
