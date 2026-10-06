import json
import sqlite3

import pytest

from fbscout.dataset import Dataset
from test_dataset import make_run, rec  # noqa: F401  (shared fixtures/helpers)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("FBSCOUT_DB", raising=False)
    monkeypatch.setenv("FBSCOUT_HOME", str(tmp_path / "home"))
    return tmp_path / "out"


def _two_items(root):
    make_run(root, "brand-x", "20261001-100000", [
        rec("a", text="Brand X inverter stopped working, service centre did nothing, switching to Brand Y",
            post_url="https://www.facebook.com/reel/1/", kind="reel", keyword="Brand X",
            time_exact="Monday 7 September 2026 at 10:00"),
        rec("b", text="Brand X inverters new stock, best price, contact now",
            post_url="https://www.facebook.com/reel/2/", kind="reel", keyword="Brand X",
            time_exact="Thursday 1 October 2026 at 10:00"),
    ], keyword="Brand X")
    ds = Dataset(root / "fbscout.sqlite")
    ds.import_all(root)
    items = ds.items(limit=None)
    complaint = next(i["item_id"] for i in items if "switching" in i["text"])
    ad = next(i["item_id"] for i in items if "new stock" in i["text"])
    return ds, complaint, ad


def test_methods_side_by_side_and_primary_order(root):
    ds, complaint, ad = _two_items(root)
    with ds:
        ds.annotate([{"item_id": complaint, "sentiment": "positive", "reason": "score 0.6"}], method="model",
                    model="xlmr")
        assert ds.items(sentiment="positive")[0]["label_method"] == "model"   # the only label so far
        ds.annotate([{"item_id": complaint, "sentiment": "negative", "churn": "switched", "churn_target": "Brand Y",
                      "aspects": "durability:negative; customer_service:negative", "feedback_type": "complaint",
                      "reason": "stopped working, switching to Brand Y"}], method="agent")
        ds.annotate([{"item_id": complaint, "sentiment": "neutral"}], method="human", annotator="A")
        item = ds.items(keyword="Brand X", limit=None, sentiment="negative")[0]
        assert item["item_id"] == complaint and item["label_method"] == "agent"   # agent > model; A never primary
        assert item["churn"] == "switched" and item["churn_target"] == "Brand Y"
        assert [a["aspect"] for a in item["aspects"]] == ["durability", "customer_service"]
        assert {(a["method"], a["annotator"]) for a in item["annotations"]} == {("model", ""), ("agent", ""),
                                                                                 ("human", "A")}
        assert ds.count(sentiment="positive", method="model") == 1                # one method's own labels
        assert ds.count(sentiment="neutral", method="human:A") == 1
        ds.annotate([{"item_id": complaint, "sentiment": "neutral"}], method="claude-api", model="claude-opus-5-5")
        assert ds.items(sentiment="neutral")[0]["label_method"] == "claude-api"   # claude-api > agent
        ds.annotate([{"item_id": complaint, "sentiment": "negative"}], method="human", annotator="gold")
        assert ds.items(sentiment="negative")[0]["label_method"] == "human"       # gold beats everything
        with pytest.raises(ValueError, match="annotator"):
            ds.annotate([{"item_id": complaint, "sentiment": "negative"}], method="human")
        with pytest.raises(ValueError, match="method"):
            ds.count(sentiment="negative", method="robot")


def test_filters_stats_and_queue(root):
    ds, complaint, ad = _two_items(root)
    with ds:
        assert ds.label_queue(method="claude-api")["remaining"] == 2
        ds.annotate([
            {"item_id": complaint, "sentiment": "negative", "aspects": "durability:negative; customer_service:negative",
             "churn": "switched", "churn_target": "Brand Y", "feedback_type": "complaint"},
            {"item_id": ad, "sentiment": "neutral", "aspects": "", "churn": "none", "feedback_type": "advertisement"},
        ], method="agent")
        assert ds.label_queue(method="agent")["remaining"] == 0
        assert ds.label_queue(method="claude-api")["remaining"] == 2          # per method
        assert ds.count(aspect="customer_service") == 1
        assert ds.count(churn="considering,switched") == 1
        assert ds.count(feedback_type="advertisement") == 1
        assert ds.count(aspect="price") == 0
        st = ds.label_stats(keyword="brand x")
        assert st["labeled"] == 2 and st["by_sentiment"] == {"negative": 1, "neutral": 1}
        assert st["by_aspect"] == {"customer_service": {"negative": 1}, "durability": {"negative": 1}}
        assert st["by_churn"] == {"none": 1, "switched": 1} and st["churn_targets"] == {"Brand Y": 1}
        assert st["sentiment_by_month"] == {"2026-09": {"negative": 1}, "2026-10": {"neutral": 1}}
        assert st["labels_by_method"] == {"agent": 2}
        full = ds.stats()
        assert full["by_sentiment"] == {"negative": 1, "neutral": 1} and full["not_labeled"] == 0


def test_v03_labels_are_migrated(root):
    _two_items(root)[0].close()
    db = root / "fbscout.sqlite"
    con = sqlite3.connect(db)
    item_id = con.execute("SELECT item_id FROM items LIMIT 1").fetchone()[0]
    con.execute("CREATE TABLE labels (item_id TEXT NOT NULL, keyword TEXT NOT NULL COLLATE NOCASE, "
                "sentiment TEXT NOT NULL, reason TEXT, labeler TEXT, labeled_at TEXT, PRIMARY KEY (item_id, keyword))")
    con.execute("INSERT INTO labels VALUES (?, 'Brand X', 'negative', 'old', 'claude', '2026-10-06T10:00:00Z')", (item_id,))
    con.commit()
    con.close()
    (root / "labels.json").write_text(json.dumps({"labels": [
        {"item_id": item_id, "keyword": "Brand X", "sentiment": "negative", "reason": "old", "labeler": "claude",
         "labeled_at": "2026-10-06T10:00:00Z", "refs": [["brand-x_20261001-100000", "a"]]}]}), encoding="utf-8")
    with Dataset(db) as ds:
        assert not ds.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'labels'").fetchone()
        rows = ds.annotations()
        assert len(rows) == 1 and rows[0]["method"] == "agent" and rows[0]["model"] == "claude"
        assert rows[0]["aspects"] is None                                   # not judged in v0.3
    db.unlink()                                                             # rebuild: restored from labels.json
    with Dataset(db) as ds:
        ds.import_all(root)
        assert [(a["method"], a["sentiment"]) for a in ds.annotations()] == [("agent", "negative")]
