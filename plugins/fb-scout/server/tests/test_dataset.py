import csv
import json

import pytest

from fbscout import api
from fbscout.dataset import Dataset, content_key, url_key
from fbscout.export import export_items


def make_run(root, slug, stamp, records, keyword="solar panel", offset=300, **run_extra):
    d = root / slug / stamp
    (d / "screenshots").mkdir(parents=True)
    run = {"tool": "fb-scout", "version": "0.2.0", "run_id": f"{slug}_{stamp}", "keyword": keyword,
           "scope": "search:posts", "status": "completed", "started_at": "2026-10-01T10:00:00Z",
           "browser_utc_offset_minutes": offset, "stats": {"saved": len(records)}, "warnings": [], **run_extra}
    (d / "results.json").write_text(json.dumps({"run": run, "results": records}), encoding="utf-8")
    return d


def rec(rid, **kw):
    base = {"id": rid, "kind": "post", "keyword": "solar panel", "keyword_verified": True, "text": "",
            "captured_at": "2026-10-01T10:00:00Z", "screenshot_path": f"screenshots/{rid}.png", "source": "search:posts"}
    base.update(kw)
    return base


# Long enough that Facebook would cut it behind "See more" (the fingerprint uses the first 160 letters).
LONG = ("Selling solar panel stock in Karachi at wholesale rates, all brands available: Jinko, Longi, Canadian. "
        "Delivery all over Pakistan, warranty card with every panel. Contact 0300-0000000 for details")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("FBSCOUT_DB", raising=False)
    monkeypatch.setenv("FBSCOUT_HOME", str(tmp_path / "home"))
    return tmp_path / "out"


def test_url_key_is_stable_only_for_stable_urls():
    assert url_key(rec("a", post_url="https://www.facebook.com/groups/9/?multi_permalinks=55&__cft__[0]=x")) \
        == url_key(rec("b", post_url="https://www.facebook.com/groups/9/posts/55/")) == "url:/groups/9/posts/55/"
    assert url_key(rec("a", kind="reel", post_url="https://www.facebook.com/reel/123/?s=fb_shorts_tab")) == "url:/reel/123/"
    assert url_key(rec("a", post_url="https://www.facebook.com/permalink.php?story_fbid=pfbid02abc&id=7")) is None
    assert url_key(rec("a", post_url="https://www.facebook.com/SolarCo/posts/pfbid02abc/")) is None
    assert url_key(rec("a", post_url="https://www.facebook.com/share/p/AbCd/")) is None
    assert url_key(rec("a", post_url="https://www.facebook.com/permalink.php?story_fbid=42&id=7")) is not None
    assert url_key(rec("a", kind="photo", post_url="https://www.facebook.com/photo/?fbid=5&set=a.1")) \
        == url_key(rec("b", kind="photo", post_url="https://www.facebook.com/photo/?fbid=5&set=gm.9"))
    # Comments: the comment id identifies them, whatever form the post URL has.
    c1 = rec("c", kind="comment", comment_url="https://www.facebook.com/x/posts/pfbid1/?comment_id=1111")
    c2 = rec("d", kind="comment", comment_url="https://www.facebook.com/permalink.php?story_fbid=pfbid2&id=7&comment_id=1111")
    assert url_key(c1) == url_key(c2) == "comment:1111"
    # base64 'comment:55_1111' is the same comment
    c3 = rec("e", kind="comment", comment_url="https://www.facebook.com/x/?comment_id=Y29tbWVudDo1NV8xMTEx")
    assert url_key(c3) == "comment:1111"
    reply = rec("f", kind="reply", comment_url="https://www.facebook.com/groups/1/posts/2/?comment_id=3&reply_comment_id=4")
    assert url_key(reply) == "comment:4"


def test_content_key_ignores_spacing_and_needs_enough_text():
    a = rec("a", text="New  Solar Panel range launched today!! 🌞", group_url="https://www.facebook.com/groups/9/")
    b = rec("b", text="new solar panel range launched today", group_url="https://www.facebook.com/groups/9/")
    other_group = rec("c", text=a["text"], group_url="https://www.facebook.com/groups/10/")
    assert content_key(a) == content_key(b) != content_key(other_group)
    assert content_key(rec("d", text="Interested")) is None


def test_import_merges_across_runs_and_is_idempotent(root):
    pfbid_1 = rec("r1", post_url="https://www.facebook.com/permalink.php?story_fbid=pfbid0AAA&id=100000000000001",
                  author_name="Online status indicator", text=LONG, time_exact="Saturday 26 September 2026 at 08:52")
    group_old = rec("r2", post_url="https://www.facebook.com/groups/599/?multi_permalinks=186", kind="group_post",
                    text="SOLAR PANEL MONTHLY CARE PLANS", time_text="May be an image of text")
    photo = rec("r3", kind="photo", post_url="https://www.facebook.com/photo/?fbid=77&set=pcb.555",
                group_url="https://www.facebook.com/groups/123/", text="Short text but long enough to identify",
                author_name="Sana Traders")
    make_run(root, "solar-panel", "20261002-140000", [pfbid_1, group_old, photo])

    pfbid_2 = rec("r9", post_url="https://www.facebook.com/permalink.php?story_fbid=pfbid0BBB&id=100000000000001",
                  author_name="Sunny Solar Shop", text=LONG + " See more details inside", captured_at="2026-10-06T07:00:00Z")
    group_new = rec("r8", post_url="https://www.facebook.com/groups/599/posts/186/", kind="group_post",
                    text="SOLAR PANEL MONTHLY CARE PLANS", time_exact="Monday 7 September 2026 at 10:39",
                    captured_at="2026-10-06T07:00:00Z", author_name="Ayesha")
    same_text_other_author = rec("r7", post_url="https://www.facebook.com/someone/posts/pfbid0CCC/",
                                 author_name="Someone Else", text=LONG, captured_at="2026-10-06T07:00:00Z")
    make_run(root, "solar-panel", "20261006-070000", [pfbid_2, group_new, same_text_other_author],
             batch_id="study_1")

    with Dataset(root / "fbscout.sqlite") as ds:
        first = ds.import_all(root)
        assert first == {"runs": 2, "records": 6, "new_items": 4, "merged": 2, "already_imported": 0, "excluded": 0,
                         "errors": []}
        again = ds.import_all(root)
        assert again["new_items"] == 0 and again["merged"] == 0 and again["already_imported"] == 6
        assert ds.count() == 4

        items = {i["text"][:20]: i for i in ds.items(limit=None, text_chars=None)}
        shop = next(i for i in ds.items(limit=None, text_chars=None) if i["author_name"] == "Sunny Solar Shop")
        assert shop["times_seen"] == 2                              # pfbid changed, still one post
        assert shop["text"].endswith("See more details inside")      # the longer (expanded) text wins
        assert shop["posted_at"] == "2026-09-26T08:52:00+05:00"
        assert shop["first_seen"] == "2026-10-01T10:00:00Z" and shop["last_seen"] == "2026-10-06T07:00:00Z"

        care = items["SOLAR PANEL MONTHLY "]
        assert care["times_seen"] == 2 and care["post_url"] == "https://www.facebook.com/groups/599/posts/186/"
        assert care["time_text"] is None                           # the old image description was dropped
        assert care["author_name"] == "Ayesha"                     # filled in from the later run
        assert care["posted_date"] == "2026-09-07"

        sana = items["Short text but long "]
        assert sana["kind"] == "group_post"                        # derived from the photo's set=pcb.<id>
        assert sana["post_url"] == "https://www.facebook.com/groups/123/posts/555/"
        assert sana["screenshot_path"] == "solar-panel/20261002-140000/screenshots/r3.png"

        assert [i["author_name"] for i in ds.items(limit=None) if i["text"].startswith("Selling")].count("Someone Else") == 1

        stats = ds.stats()
        assert stats["items"] == 4 and stats["runs"] == 2 and stats["seen_in_more_than_one_run"] == 2
        assert stats["by_keyword"] == {"solar panel": 4}
        assert stats["by_kind"] == {"group_post": 2, "post": 2}
        run = ds.conn.execute("SELECT batch_id FROM runs WHERE run_id = 'solar-panel_20261006-070000'").fetchone()
        assert run[0] == "study_1"


def test_filters_and_keywords(root):
    make_run(root, "solar-panel", "20261001-100000", [
        rec("a", text="Solar panel ki price kya hai bhai", kind="group_post", group_name="Solar Users",
            post_url="https://www.facebook.com/groups/1/posts/1/", time_exact="1 September 2026 at 10:00"),
        rec("b", text="Best solar panel for the home", post_url="https://www.facebook.com/reel/5/", kind="reel",
            time_exact="1 October 2026 at 10:00"),
    ])
    make_run(root, "inverter", "20261001-110000", [
        rec("c", text="Solar panel ki price kya hai bhai", kind="group_post", keyword="inverter",
            post_url="https://www.facebook.com/groups/1/posts/1/"),
    ], keyword="inverter")
    with Dataset(root / "fbscout.sqlite") as ds:
        ds.import_all(root)
        assert ds.count() == 2
        both = ds.items(keyword="inverter")
        assert len(both) == 1 and both[0]["keywords"] == ["inverter", "solar panel"]
        assert [i["item_id"] for i in ds.items(language="ur-Latn")] == [both[0]["item_id"]]
        assert ds.count(kind="reel,post") == 1
        assert ds.count(since="2026-09-15") == 1 and ds.count(until="2026-09-15") == 1
        assert ds.count(group="solar users") == 1
        assert ds.count(contains="home") == 1
        assert ds.items()[0]["kind"] == "reel"                      # newest post first
        assert ds.stats(keyword="inverter")["items"] == 1


def test_export_csv_jsonl_and_anonymized(root, tmp_path):
    make_run(root, "solar-panel", "20261001-100000", [
        rec("a", text="سولر پینل کی قیمت کیا ہے؟", author_name="Ali Khan",
            author_url="https://www.facebook.com/ali.khan", post_url="https://www.facebook.com/reel/5/", kind="reel"),
        rec("b", text="Anyone know a good solar panel installer?", author_name="Sara",
            author_url="https://www.facebook.com/sara", post_url="https://www.facebook.com/reel/6/", kind="reel"),
    ])
    with Dataset(root / "fbscout.sqlite") as ds:
        ds.import_all(root)
        out = export_items(ds, tmp_path / "x.csv", "csv")
        assert out["rows"] == 2
        raw = (tmp_path / "x.csv").read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")                     # BOM: Excel reads Urdu correctly
        rows = list(csv.DictReader((tmp_path / "x.csv").open(encoding="utf-8-sig")))
        assert {r["language"] for r in rows} == {"ur", "en"} and rows[0]["keywords"] == "solar panel"

        anon = export_items(ds, tmp_path / "a.jsonl", "jsonl", anonymize=True)
        lines = [json.loads(l) for l in (tmp_path / "a.jsonl").read_text(encoding="utf-8").splitlines()]
        assert anon["anonymized"] and len(lines) == 2 and anon["warnings"]
        text = json.dumps(lines, ensure_ascii=False)
        assert "Ali Khan" not in text and "facebook.com" not in text
        assert all(l["author_id"].startswith("a_") for l in lines)
        again = export_items(ds, tmp_path / "b.jsonl", "jsonl", anonymize=True)
        assert again["rows"] == 2
        ids = [json.loads(l)["author_id"] for l in (tmp_path / "b.jsonl").read_text(encoding="utf-8").splitlines()]
        assert ids == [l["author_id"] for l in lines]              # stable pseudonyms (same salt)

        with pytest.raises(ValueError):
            export_items(ds, tmp_path / "x.xml", "xml")


def test_api_creates_dataset_from_existing_runs(root):
    assert api.dataset_stats(str(root))["error"] == "no_dataset"
    make_run(root, "solar-panel", "20261001-100000", [rec("a", text=LONG, post_url="https://www.facebook.com/reel/5/",
                                                          kind="reel")])
    stats = api.dataset_stats(str(root))
    assert stats["ok"] and stats["items"] == 1 and (root / "fbscout.sqlite").exists()

    items = api.dataset_items(str(root))
    assert items["total"] == 1 and "url_key" not in items["items"][0]
    assert items["items"][0]["screenshot_file"].endswith("a.png")

    exported = api.dataset_export(str(root), "csv")
    assert exported["ok"] and "_exports" in exported["file"]
    assert api.dataset_export(str(root), "parquet").get("error") in (None, "invalid_argument")


def test_exclusions_survive_reimport_and_rebuild(root):
    make_run(root, "solar-panel", "20261001-100000", [
        rec("a", text=LONG, post_url="https://www.facebook.com/reel/5/", kind="reel"),
        rec("b", text="Sunbright Traders @solarpanel.pk Lives in Pakistan", kind="group_post"),
    ])
    make_run(root, "solar-panel", "20261002-100000", [
        rec("c", text="Sunbright Traders @solarpanel.pk Lives in Pakistan", kind="group_post"),
    ])
    db = root / "fbscout.sqlite"
    with Dataset(db) as ds:
        ds.import_all(root)
        card = next(i for i in ds.items(limit=None) if i["text"].startswith("Sunbright"))
        assert card["times_seen"] == 2
        with pytest.raises(ValueError, match="reason"):
            ds.exclude_items([card["item_id"]], " ")
        result = ds.exclude_items([card["item_id"], "i_doesnotexist"], "member card, not a post")
        assert result["excluded_items"] == 1 and result["excluded_records"] == 2
        assert result["not_found"] == ["i_doesnotexist"] and result["items_left"] == 1

        again = ds.import_all(root)                                  # stays out on re-import
        assert again["excluded"] == 2 and again["new_items"] == 0 and ds.count() == 1
        assert ds.stats()["excluded_records"] == 2

    saved = json.loads((root / "exclusions.json").read_text(encoding="utf-8"))["exclusions"]
    assert {(e["run_id"], e["record_id"]) for e in saved} == {("solar-panel_20261001-100000", "b"),
                                                               ("solar-panel_20261002-100000", "c")}
    assert all(e["reason"] == "member card, not a post" for e in saved)
    assert (root / "solar-panel" / "20261001-100000" / "results.json").read_text(encoding="utf-8").count('"b"') == 1

    db.unlink()                                                      # rebuilt from the run folders: still out
    with Dataset(db) as ds:
        assert ds.import_all(root)["excluded"] == 2 and ds.count() == 1

    (root / "exclusions.json").write_text('{"exclusions": []}', encoding="utf-8")   # restore by deleting the entry
    with Dataset(db) as ds:
        ds.import_all(root)
        assert ds.count() == 2


def test_api_exclude(root):
    make_run(root, "solar-panel", "20261001-100000", [rec("a", text=LONG, post_url="https://www.facebook.com/reel/5/",
                                                          kind="reel")])
    item_id = api.dataset_items(str(root))["items"][0]["item_id"]
    assert api.dataset_exclude([item_id], "", str(root))["error"] == "invalid_argument"
    out = api.dataset_exclude([item_id], "not about the brand", str(root))
    assert out["ok"] and out["items_left"] == 0


def _labelled_dataset(root):
    make_run(root, "brand-x", "20261001-100000", [
        rec("a", text="Brand X inverter stopped working after 2 weeks, worst service, bilkul bekar",
            post_url="https://www.facebook.com/reel/1/", kind="reel", keyword="Brand X"),
        rec("b", text="Brand X inverter new stock available, price 75000, contact now",
            post_url="https://www.facebook.com/reel/2/", kind="reel", keyword="Brand X"),
        rec("c", text="Comparing Brand X and Brand Y inverters: Y failed twice, X has been perfect",
            post_url="https://www.facebook.com/reel/3/", kind="reel", keyword="Brand X"),
    ], keyword="Brand X")
    make_run(root, "brand-y", "20261001-110000", [
        rec("c2", text="Comparing Brand X and Brand Y inverters: Y failed twice, X has been perfect",
            post_url="https://www.facebook.com/reel/3/", kind="reel", keyword="Brand Y"),
        rec("m", text="Solar inverter 5kW Brand Y", post_url="https://www.facebook.com/marketplace/item/77/?ref=search",
            kind="marketplace", keyword="Brand Y", price="PKR75,000", location="Karachi, Pakistan"),
    ], keyword="Brand Y")


def test_sentiment_labels(root):
    _labelled_dataset(root)
    db = root / "fbscout.sqlite"
    with Dataset(db) as ds:
        ds.import_all(root)
        complaint = next(i for i in ds.items(limit=None) if "worst" in i["text"])["item_id"]
        ad = next(i for i in ds.items(limit=None) if "new stock" in i["text"])["item_id"]
        both = next(i for i in ds.items(limit=None) if "Comparing" in i["text"])["item_id"]
        listing = next(i for i in ds.items(limit=None) if i["kind"] == "marketplace")["item_id"]

        queue = ds.label_queue(limit=50)
        assert queue["remaining"] == 5                              # the comparison needs a label per keyword
        assert {(q["item_id"], q["keyword"]) for q in queue["to_label"]} >= {(both, "Brand X"), (both, "Brand Y")}
        assert ds.label_queue(run_id="brand-x_20261001-100000")["remaining"] == 3

        result = ds.label_items([
            {"item_id": complaint, "sentiment": "negative", "reason": "'worst service', 'bilkul bekar'"},
            {"item_id": ad, "sentiment": "Neutral", "reason": "sale ad"},
            {"item_id": both, "sentiment": "positive", "keyword": "brand x", "reason": "X has been perfect"},
            {"item_id": both, "sentiment": "negative", "keyword": "Brand Y", "reason": "Y failed twice"},
            {"item_id": both, "sentiment": "negative"},                          # which keyword?
            {"item_id": listing, "sentiment": "angry"},                          # not a label
            {"item_id": "i_nope", "sentiment": "negative"},
        ])
        assert result["labeled"] == 4 and len(result["errors"]) == 3
        assert ds.label_queue()["remaining"] == 1                   # only the listing is left

        negatives_x = ds.items(keyword="Brand X", sentiment="negative")
        assert [i["item_id"] for i in negatives_x] == [complaint]
        assert negatives_x[0]["sentiment"] == "negative" and "bekar" in negatives_x[0]["sentiment_reason"]
        assert {i["item_id"] for i in ds.items(sentiment="negative")} == {complaint, both}
        assert [i["item_id"] for i in ds.items(keyword="Brand Y", sentiment="negative")] == [both]
        mixed = next(i for i in ds.items(limit=None) if i["item_id"] == both)
        assert mixed["sentiment"] == "Brand X: positive | Brand Y: negative"

        stats = ds.stats(keyword="Brand X")
        assert stats["by_sentiment"] == {"negative": 1, "neutral": 1, "positive": 1} and stats["not_labeled"] == 0
        assert ds.stats()["not_labeled"] == 1
        assert ds.count(kind="marketplace", contains="PKR75") == 1

        out = export_items(ds, root / "neg.csv", "csv", sentiment="negative", keyword="Brand X")
        rows = list(csv.DictReader((root / "neg.csv").open(encoding="utf-8-sig")))
        assert out["rows"] == 1 and rows[0]["sentiment"] == "negative" and "price" in rows[0]

    saved = json.loads((root / "labels.json").read_text(encoding="utf-8"))["labels"]
    assert len(saved) == 4 and all(e["refs"] for e in saved)

    db.unlink()                                                     # rebuilt from the run folders
    with Dataset(db) as ds:
        ds.import_all(root)
        assert ds.stats()["by_sentiment"] == {"negative": 2, "neutral": 1, "positive": 1}
        ds.exclude_items([complaint], "test")                       # removing an item drops its label
        assert ds.count(sentiment="negative", keyword="Brand X") == 0


def test_old_dataset_file_gets_new_columns(tmp_path):
    import sqlite3
    db = tmp_path / "fbscout.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE items (item_id TEXT PRIMARY KEY, url_key TEXT UNIQUE, content_key TEXT, kind TEXT, "
                "post_url TEXT, comment_url TEXT, parent_post_url TEXT, author_name TEXT, author_url TEXT, "
                "group_name TEXT, group_url TEXT, text TEXT, image_text TEXT, time_text TEXT, time_exact TEXT, "
                "posted_at TEXT, posted_date TEXT, posted_at_precision TEXT, posted_at_source TEXT, language TEXT, "
                "screenshot_path TEXT, first_seen TEXT, last_seen TEXT, times_seen INTEGER NOT NULL DEFAULT 0, "
                "first_run_id TEXT, last_run_id TEXT)")
    con.commit()
    con.close()
    with Dataset(db) as ds:
        cols = {r[1] for r in ds.conn.execute("PRAGMA table_info(items)")}
        assert {"price", "location", "condition"} <= cols
        assert ds.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "3"


def test_profile_cards_dedupe_by_profile():
    a = rec("a", kind="profile", post_url="https://www.facebook.com/solarpanel.ali/?__tn__=x", text="Ali")
    b = rec("b", kind="profile", author_url="https://www.facebook.com/solarpanel.ali", text="Ali (Solar Panel expert)")
    assert url_key(a) == url_key(b) and url_key(a).startswith("profile:")


def test_api_labels_and_only_negative_next_step(root, monkeypatch):
    import asyncio
    from fbscout.scraper import SearchOptions

    run_dir = make_run(root, "brand-x", "20261001-100000", [
        rec("a", text="Brand X inverter is bekar, stopped working", post_url="https://www.facebook.com/reel/1/",
            kind="reel", keyword="Brand X")], keyword="Brand X")

    async def fake_run_search(opts, progress=None):
        return {"ok": True, "status": "completed", "run_id": "brand-x_20261001-100000", "run_dir": str(run_dir)}

    monkeypatch.setattr(api, "run_search", fake_run_search)
    result = asyncio.run(api._search_and_import(SearchOptions(keyword="Brand X", output_dir=str(root), only_negative=True)))
    assert "fb_label_queue" in result["next_step"] and "brand-x_20261001-100000" in result["next_step"]
    plain = asyncio.run(api._search_and_import(SearchOptions(keyword="Brand X", output_dir=str(root))))
    assert "next_step" not in plain

    queue = api.label_queue(str(root), run_id="brand-x_20261001-100000")
    assert queue["remaining"] == 1 and queue["to_label"][0]["screenshot_file"].endswith("a.png")
    item_id = queue["to_label"][0]["item_id"]
    assert not api.label_items([{"item_id": item_id, "sentiment": "bad"}], str(root))["ok"]
    assert api.label_items([{"item_id": item_id, "sentiment": "negative", "reason": "bekar"}], str(root))["ok"]
    negatives = api.dataset_items(str(root), run_id="brand-x_20261001-100000", sentiment="negative")
    assert negatives["total"] == 1 and negatives["items"][0]["sentiment_reason"] == "bekar"
