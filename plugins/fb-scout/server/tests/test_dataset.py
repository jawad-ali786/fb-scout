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
        assert first == {"runs": 2, "records": 6, "new_items": 4, "merged": 2, "already_imported": 0, "errors": []}
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
